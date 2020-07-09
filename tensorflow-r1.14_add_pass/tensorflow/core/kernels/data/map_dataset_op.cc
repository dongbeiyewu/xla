/* Copyright 2017 The TensorFlow Authors. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
==============================================================================*/
#include "tensorflow/core/common_runtime/function.h"
#include "tensorflow/core/framework/dataset.h"
#include "tensorflow/core/framework/partial_tensor_shape.h"
#include "tensorflow/core/framework/tensor.h"
#include "tensorflow/core/kernels/data/captured_function.h"
#include "tensorflow/core/kernels/data/dataset_utils.h"
#include "tensorflow/core/lib/random/random.h"

namespace tensorflow {
namespace data {
namespace {

// See documentation in ../../ops/dataset_ops.cc for a high-level
// description of the following op.

class MapDatasetOp : public UnaryDatasetOpKernel {
 public:
  using MapIteratorFunction =
      std::function<Status(IteratorContext*, InstantiatedCapturedFunction*,
                           std::vector<Tensor>, std::vector<Tensor>*)>;

  explicit MapDatasetOp(OpKernelConstruction* ctx) : UnaryDatasetOpKernel(ctx) {
    FunctionMetadata::Params params;
    OP_REQUIRES_OK(ctx, ctx->GetAttr("use_inter_op_parallelism",
                                     &params.use_inter_op_parallelism));
    OP_REQUIRES_OK(ctx,
                   FunctionMetadata::Create(ctx, "f", params, &func_metadata_));
    OP_REQUIRES_OK(ctx, ctx->GetAttr("output_types", &output_types_));
    OP_REQUIRES_OK(ctx, ctx->GetAttr("output_shapes", &output_shapes_));
    OP_REQUIRES_OK(
        ctx, ctx->GetAttr("preserve_cardinality", &preserve_cardinality_));
  }

  void MakeDataset(OpKernelContext* ctx, DatasetBase* input,
                   DatasetBase** output) override {
    std::unique_ptr<CapturedFunction> captured_func;
    OP_REQUIRES_OK(
        ctx, CapturedFunction::Create(ctx, func_metadata_, "other_arguments",
                                      &captured_func));

    *output = new Dataset(ctx, input, std::move(captured_func), output_types_,
                          output_shapes_, preserve_cardinality_);
  }

 private:
  class Dataset : public DatasetBase {
   public:
    Dataset(OpKernelContext* ctx, const DatasetBase* input,
            std::unique_ptr<CapturedFunction> captured_func,
            const DataTypeVector& output_types,
            const std::vector<PartialTensorShape>& output_shapes,
            bool preserve_cardinality)
        : DatasetBase(DatasetContext(ctx)),
          input_(input),
          preserve_cardinality_(preserve_cardinality),
          captured_func_(std::move(captured_func)),
          output_types_(output_types),
          output_shapes_(output_shapes) {
      input_->Ref();
    }

    ~Dataset() override { input_->Unref(); }

    std::unique_ptr<IteratorBase> MakeIteratorInternal(
        const string& prefix) const override {
      return absl::make_unique<Iterator>(
          Iterator::Params{this, strings::StrCat(prefix, "::Map")});
    }

    const DataTypeVector& output_dtypes() const override {
      return output_types_;
    }
    const std::vector<PartialTensorShape>& output_shapes() const override {
      return output_shapes_;
    }

    string DebugString() const override { return "MapDatasetOp::Dataset"; }

    int64 Cardinality() const override { return input_->Cardinality(); }

   protected:
    Status AsGraphDefInternal(SerializationContext* ctx,
                              DatasetGraphDefBuilder* b,
                              Node** output) const override {
      Node* input_graph_node = nullptr;
      TF_RETURN_IF_ERROR(b->AddInputDataset(ctx, input_, &input_graph_node));

      std::vector<Node*> other_arguments;
      DataTypeVector other_arguments_types;
      TF_RETURN_IF_ERROR(captured_func_->AddToGraph(ctx, b, &other_arguments,
                                                    &other_arguments_types));

      // Attr: f
      AttrValue f_attr;
      b->BuildAttrValue(captured_func_->func(), &f_attr);

      // Attr: Targuments
      AttrValue other_arguments_types_attr;
      b->BuildAttrValue(other_arguments_types, &other_arguments_types_attr);

      // Attr: use_inter_op_parallelism
      AttrValue use_inter_op_parallelism_attr;
      b->BuildAttrValue(captured_func_->use_inter_op_parallelism(),
                        &use_inter_op_parallelism_attr);

      // Attr: preserve_cardinality
      AttrValue preserve_cardinality_attr;
      b->BuildAttrValue(preserve_cardinality_, &preserve_cardinality_attr);

      TF_RETURN_IF_ERROR(b->AddDataset(
          this, {std::make_pair(0, input_graph_node)},  // Single tensor inputs.
          {std::make_pair(1, other_arguments)},         // Tensor list inputs.
          {std::make_pair("f", f_attr),
           std::make_pair("Targuments", other_arguments_types_attr),
           std::make_pair("use_inter_op_parallelism",
                          use_inter_op_parallelism_attr),
           std::make_pair("preserve_cardinality",
                          preserve_cardinality_attr)},  // Attrs
          output));
      return Status::OK();
    }

   private:
    class Iterator : public DatasetIterator<Dataset> {
     public:
      explicit Iterator(const Params& params)
          : DatasetIterator<Dataset>(params) {}

      Status Initialize(IteratorContext* ctx) override {
        TF_RETURN_IF_ERROR(
            dataset()->input_->MakeIterator(ctx, prefix(), &input_impl_));
        return dataset()->captured_func_->Instantiate(
            ctx, &instantiated_captured_func_);
      }

      Status GetNextInternal(IteratorContext* ctx,
                             std::vector<Tensor>* out_tensors,
                             bool* end_of_sequence) override {
        // NOTE(mrry): This method is thread-safe as long as
        // `input_impl_` and `f` are thread-safe. However, if multiple
        // threads enter this method, outputs may be observed in a
        // non-deterministic order.

        std::vector<Tensor> args;
        TF_RETURN_IF_ERROR(input_impl_->GetNext(ctx, &args, end_of_sequence));
        if (*end_of_sequence) {
          return Status::OK();
        }

        Status s =
            instantiated_captured_func_->Run(ctx, std::move(args), out_tensors);
        if (errors::IsOutOfRange(s)) {
          if (dataset()->preserve_cardinality_) {
            // To guarantee that the transformation preserves the cardinality of
            // the dataset, we convert `OutOfRange` to `InvalidArgument` as the
            // former may be interpreted by a caller as the end of sequence.
            return errors::InvalidArgument(
                "Function invocation produced OutOfRangeError: ",
                s.error_message());
          } else {
            // `f` may deliberately raise `errors::OutOfRange` to indicate
            // that we should terminate the iteration early.
            *end_of_sequence = true;
            return Status::OK();
          }
        } else {
          return s;
        }
      }

     protected:
      std::shared_ptr<model::Node> CreateNode(
          IteratorContext* ctx, model::Node::Args args) const override {
        return model::MakeKnownRatioNode(std::move(args),
                                         /*ratio=*/1);
      }

      Status SaveInternal(IteratorStateWriter* writer) override {
        TF_RETURN_IF_ERROR(SaveInput(writer, input_impl_));
        return Status::OK();
      }

      Status RestoreInternal(IteratorContext* ctx,
                             IteratorStateReader* reader) override {
        TF_RETURN_IF_ERROR(RestoreInput(ctx, reader, input_impl_));
        return Status::OK();
      }

     private:
      std::unique_ptr<IteratorBase> input_impl_;
      std::unique_ptr<InstantiatedCapturedFunction> instantiated_captured_func_;
    };

    const DatasetBase* const input_;
    const bool preserve_cardinality_;
    const std::unique_ptr<CapturedFunction> captured_func_;
    const DataTypeVector output_types_;
    const std::vector<PartialTensorShape> output_shapes_;
  };

  std::shared_ptr<FunctionMetadata> func_metadata_ = nullptr;
  DataTypeVector output_types_;
  std::vector<PartialTensorShape> output_shapes_;
  bool preserve_cardinality_;
};

REGISTER_KERNEL_BUILDER(Name("MapDataset").Device(DEVICE_CPU), MapDatasetOp);
REGISTER_KERNEL_BUILDER(Name("ExperimentalMapDataset")
                            .Device(DEVICE_GPU)
                            .HostMemory("input_dataset")
                            .HostMemory("handle"),
                        MapDatasetOp);

}  // namespace
}  // namespace data
}  // namespace tensorflow
