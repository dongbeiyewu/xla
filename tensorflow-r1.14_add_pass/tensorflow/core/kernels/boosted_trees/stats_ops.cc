/* Copyright 2018 The TensorFlow Authors. All Rights Reserved.

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

#include <vector>

#include "third_party/eigen3/Eigen/Core"
#include "tensorflow/core/framework/op_kernel.h"
#include "tensorflow/core/framework/tensor.h"
#include "tensorflow/core/kernels/boosted_trees/tree_helper.h"
#include "tensorflow/core/platform/logging.h"

namespace tensorflow {

const char INEQUALITY_DEFAULT_LEFT[] = "inequality_default_left";

// V1 Op. Deprecated. BoostedTreesCalculateBestFeatureSplitOp is V2.
class BoostedTreesCalculateBestGainsPerFeatureOp : public OpKernel {
 public:
  explicit BoostedTreesCalculateBestGainsPerFeatureOp(
      OpKernelConstruction* const context)
      : OpKernel(context) {
    OP_REQUIRES_OK(context, context->GetAttr("max_splits", &max_splits_));
    OP_REQUIRES_OK(context, context->GetAttr("num_features", &num_features_));
  }

  void Compute(OpKernelContext* const context) override {
    // node_id_range
    const Tensor* node_id_range_t;
    OP_REQUIRES_OK(context, context->input("node_id_range", &node_id_range_t));
    const auto node_id_range = node_id_range_t->vec<int32>();
    const int32 node_id_first = node_id_range(0);  // inclusive
    const int32 node_id_last = node_id_range(1);   // exclusive
    // stats_summary_list
    OpInputList stats_summary_list;
    OP_REQUIRES_OK(context, context->input_list("stats_summary_list",
                                                &stats_summary_list));
    const int64 num_buckets = stats_summary_list[0].dim_size(1);
    // Check for single logit: 1 gradient + 1 hessian value.
    DCHECK_EQ(stats_summary_list[0].dim_size(2), 2);
    std::vector<TTypes<float, 3>::ConstTensor> stats_summary;
    stats_summary.reserve(stats_summary_list.size());
    for (const auto& tensor : stats_summary_list) {
      stats_summary.emplace_back(tensor.tensor<float, 3>());
    }
    const Tensor* l1_t;
    OP_REQUIRES_OK(context, context->input("l1", &l1_t));
    const auto l1 = l1_t->scalar<float>()();
    const Tensor* l2_t;
    OP_REQUIRES_OK(context, context->input("l2", &l2_t));
    const auto l2 = l2_t->scalar<float>()();
    const Tensor* tree_complexity_t;
    OP_REQUIRES_OK(context,
                   context->input("tree_complexity", &tree_complexity_t));
    const auto tree_complexity = tree_complexity_t->scalar<float>()();
    const Tensor* min_node_weight_t;
    OP_REQUIRES_OK(context,
                   context->input("min_node_weight", &min_node_weight_t));
    const auto min_node_weight = min_node_weight_t->scalar<float>()();

    // Allocate output lists of tensors:
    OpOutputList output_node_ids_list;
    OP_REQUIRES_OK(
        context, context->output_list("node_ids_list", &output_node_ids_list));
    OpOutputList output_gains_list;
    OP_REQUIRES_OK(context,
                   context->output_list("gains_list", &output_gains_list));
    OpOutputList output_thresholds_list;
    OP_REQUIRES_OK(context, context->output_list("thresholds_list",
                                                 &output_thresholds_list));
    OpOutputList output_left_node_contribs_list;
    OP_REQUIRES_OK(context,
                   context->output_list("left_node_contribs_list",
                                        &output_left_node_contribs_list));
    OpOutputList output_right_node_contribs_list;
    OP_REQUIRES_OK(context,
                   context->output_list("right_node_contribs_list",
                                        &output_right_node_contribs_list));

    // Use identity later to convert float to Eigen::Matrix type for input to
    // CalculateWeightsAndGains. This op only supports single dimension logits.
    Eigen::MatrixXf identity;
    identity.setIdentity(1, 1);
    // Get the best split info per node for each feature.
    for (int feature_idx = 0; feature_idx < num_features_; ++feature_idx) {
      std::vector<float> cum_grad;
      std::vector<float> cum_hess;
      cum_grad.reserve(num_buckets);
      cum_hess.reserve(num_buckets);

      std::vector<int32> output_node_ids;
      std::vector<float> output_gains;
      std::vector<int32> output_thresholds;
      std::vector<float> output_left_node_contribs;
      std::vector<float> output_right_node_contribs;
      for (int node_id = node_id_first; node_id < node_id_last; ++node_id) {
        // Calculate gains.
        cum_grad.clear();
        cum_hess.clear();
        float total_grad = 0.0;
        float total_hess = 0.0;
        for (int bucket = 0; bucket < num_buckets; ++bucket) {
          // TODO(nponomareva): Consider multi-dimensional gradients/hessians.
          total_grad += stats_summary[feature_idx](node_id, bucket, 0);
          total_hess += stats_summary[feature_idx](node_id, bucket, 1);
          cum_grad.push_back(total_grad);
          cum_hess.push_back(total_hess);
        }
        // Check if node has enough of average hessian.
        if (total_hess < min_node_weight) {
          // Do not split the node because not enough avg hessian.
          continue;
        }
        float best_gain = std::numeric_limits<float>::lowest();
        float best_bucket = 0;
        float best_contrib_for_left = 0.0;
        float best_contrib_for_right = 0.0;
        // Parent gain.
        float parent_gain;
        Eigen::VectorXf unused(1);
        CalculateWeightsAndGains(total_grad * identity, total_hess * identity,
                                 l1, l2, &unused, &parent_gain);

        for (int bucket = 0; bucket < num_buckets; ++bucket) {
          const float cum_grad_bucket = cum_grad[bucket];
          const float cum_hess_bucket = cum_hess[bucket];
          // Left child.
          Eigen::VectorXf contrib_for_left(1);
          float gain_for_left;
          CalculateWeightsAndGains(cum_grad_bucket * identity,
                                   cum_hess_bucket * identity, l1, l2,
                                   &contrib_for_left, &gain_for_left);
          // Right child.
          // use contrib_for_right.
          Eigen::VectorXf contrib_for_right(1);
          float gain_for_right;
          CalculateWeightsAndGains((total_grad - cum_grad_bucket) * identity,
                                   (total_hess - cum_hess_bucket) * identity,
                                   l1, l2, &contrib_for_right, &gain_for_right);

          if (GainIsLarger(gain_for_left + gain_for_right, best_gain)) {
            best_gain = gain_for_left + gain_for_right;
            best_bucket = bucket;
            best_contrib_for_left = contrib_for_left[0];
            best_contrib_for_right = contrib_for_right[0];
          }
        }  // for bucket
        output_node_ids.push_back(node_id);
        // Remove the parent gain for the parent node.
        output_gains.push_back(best_gain - parent_gain);
        output_thresholds.push_back(best_bucket);
        output_left_node_contribs.push_back(best_contrib_for_left);
        output_right_node_contribs.push_back(best_contrib_for_right);
      }  // for node_id
      const int num_nodes = output_node_ids.size();
      // output_node_ids
      Tensor* output_node_ids_t;
      OP_REQUIRES_OK(context,
                     output_node_ids_list.allocate(feature_idx, {num_nodes},
                                                   &output_node_ids_t));
      auto output_node_ids_vec = output_node_ids_t->vec<int32>();
      // output_gains
      Tensor* output_gains_t;
      OP_REQUIRES_OK(context, output_gains_list.allocate(
                                  feature_idx, {num_nodes}, &output_gains_t));
      auto output_gains_vec = output_gains_t->vec<float>();
      // output_thresholds
      Tensor* output_thresholds_t;
      OP_REQUIRES_OK(context,
                     output_thresholds_list.allocate(feature_idx, {num_nodes},
                                                     &output_thresholds_t));
      auto output_thresholds_vec = output_thresholds_t->vec<int32>();
      // output_left_node_contribs
      Tensor* output_left_node_contribs_t;
      OP_REQUIRES_OK(context, output_left_node_contribs_list.allocate(
                                  feature_idx, {num_nodes, 1},
                                  &output_left_node_contribs_t));
      auto output_left_node_contribs_matrix =
          output_left_node_contribs_t->matrix<float>();
      // output_right_node_contribs
      Tensor* output_right_node_contribs_t;
      OP_REQUIRES_OK(context, output_right_node_contribs_list.allocate(
                                  feature_idx, {num_nodes, 1},
                                  &output_right_node_contribs_t));
      auto output_right_node_contribs_matrix =
          output_right_node_contribs_t->matrix<float>();
      // Sets output tensors from vectors.
      for (int i = 0; i < num_nodes; ++i) {
        output_node_ids_vec(i) = output_node_ids[i];
        // Adjust the gains to penalize by tree complexity.
        output_gains_vec(i) = output_gains[i] - tree_complexity;
        output_thresholds_vec(i) = output_thresholds[i];
        output_left_node_contribs_matrix(i, 0) = output_left_node_contribs[i];
        // This op only supports 1-dimensional logits.
        output_right_node_contribs_matrix(i, 0) = output_right_node_contribs[i];
      }
    }  // for f
  }

 private:
  int max_splits_;
  int num_features_;
};

// V1 op that only supports single dimensional logit.
REGISTER_KERNEL_BUILDER(
    Name("BoostedTreesCalculateBestGainsPerFeature").Device(DEVICE_CPU),
    BoostedTreesCalculateBestGainsPerFeatureOp);

// V2 Op.
class BoostedTreesCalculateBestFeatureSplitOp : public OpKernel {
 public:
  explicit BoostedTreesCalculateBestFeatureSplitOp(
      OpKernelConstruction* const context)
      : OpKernel(context) {
    OP_REQUIRES_OK(context, context->GetAttr("logits_dimension", &logits_dim_));
    // TODO(crawles): multiclass support.
    DCHECK_EQ(logits_dim_, 1);
  }

  void Compute(OpKernelContext* const context) override {
    // node_id_range
    const Tensor* node_id_range_t;
    OP_REQUIRES_OK(context, context->input("node_id_range", &node_id_range_t));
    const auto node_id_range = node_id_range_t->vec<int32>();
    const int32 node_id_first = node_id_range(0);  // inclusive
    const int32 node_id_last = node_id_range(1);   // exclusive

    const Tensor* stats_summary_t;
    OP_REQUIRES_OK(context, context->input("stats_summary", &stats_summary_t));
    TTypes<float, 4>::ConstTensor stats_summary =
        stats_summary_t->tensor<float, 4>();
    const int64 feature_dims = stats_summary_t->dim_size(1);
    const int64 num_buckets = stats_summary_t->dim_size(2);
    const int64 hessian_dim = stats_summary_t->dim_size(3) - logits_dim_;
    DCHECK_GT(hessian_dim, 0);

    const Tensor* l1_t;
    OP_REQUIRES_OK(context, context->input("l1", &l1_t));
    const auto l1 = l1_t->scalar<float>()();

    const Tensor* l2_t;
    OP_REQUIRES_OK(context, context->input("l2", &l2_t));
    const auto l2 = l2_t->scalar<float>()();

    const Tensor* tree_complexity_t;
    OP_REQUIRES_OK(context,
                   context->input("tree_complexity", &tree_complexity_t));
    const auto tree_complexity = tree_complexity_t->scalar<float>()();

    const Tensor* min_node_weight_t;
    OP_REQUIRES_OK(context,
                   context->input("min_node_weight", &min_node_weight_t));
    const auto min_node_weight = min_node_weight_t->scalar<float>()();

    std::vector<int32> output_node_ids;
    std::vector<float> output_gains;
    std::vector<int32> output_feature_dimensions;
    std::vector<int32> output_thresholds;
    std::vector<float> output_left_node_contribs;
    std::vector<float> output_right_node_contribs;
    std::vector<string> output_split_types;

    for (int node_id = node_id_first; node_id < node_id_last; ++node_id) {
      std::vector<Eigen::VectorXf> cum_grad;
      std::vector<Eigen::VectorXf> cum_hess;
      cum_grad.reserve(num_buckets);
      cum_hess.reserve(num_buckets);

      float best_gain = std::numeric_limits<float>::lowest();
      float best_bucket = 0;
      float best_f_dim = 0;
      string best_split_type = INEQUALITY_DEFAULT_LEFT;
      // TODO(crawles): multi-class support; as Eigen::VectorXf.
      float best_contrib_for_left = 0;
      float best_contrib_for_right = 0;
      // Parent gain.
      float parent_gain;
      Eigen::VectorXf unused(logits_dim_);

      for (int f_dim = 0; f_dim < feature_dims; ++f_dim) {
        cum_grad.clear();
        cum_hess.clear();
        Eigen::VectorXf total_grad = Eigen::VectorXf::Zero(logits_dim_);
        Eigen::VectorXf total_hess = Eigen::VectorXf::Zero(hessian_dim);
        for (int bucket = 0; bucket < num_buckets; ++bucket) {
          for (int i = 0; i < logits_dim_; ++i) {
            total_grad[i] += stats_summary(node_id, f_dim, bucket, i);
            total_hess[i] +=
                stats_summary(node_id, f_dim, bucket, logits_dim_ + i);
          }
          for (int i = logits_dim_; i < hessian_dim; ++i) {
            // Full hessian.
            total_hess[i] += stats_summary(node_id, f_dim, bucket, i);
          }
          cum_grad.push_back(total_grad);
          cum_hess.push_back(total_hess);
        }

        // TODO(crawles): Check if grad is almost zero.
        if (total_hess.norm() < min_node_weight) {
          // Do not split the node because not enough hessian.
          break;
        }
        if (f_dim == 0) {
          CalculateWeightsAndGains(total_grad, total_hess, l1, l2, &unused,
                                   &parent_gain);
        }

        for (int bucket = 0; bucket < num_buckets; ++bucket) {
          const Eigen::VectorXf cum_grad_bucket = cum_grad[bucket];
          const Eigen::VectorXf cum_hess_bucket = cum_hess[bucket];
          // Left child.
          Eigen::VectorXf contrib_for_left(logits_dim_);
          float gain_for_left;
          CalculateWeightsAndGains(cum_grad_bucket, cum_hess_bucket, l1, l2,
                                   &contrib_for_left, &gain_for_left);
          // Right child.
          // TODO(crawles): consider accumulating right grad/hessians when doing
          // cum_grad/hessian (if this becomes a bottleneck).
          const Eigen::VectorXf grad_for_right = total_grad - cum_grad_bucket;
          const Eigen::VectorXf hess_for_right = total_hess - cum_hess_bucket;
          Eigen::VectorXf contrib_for_right(logits_dim_);
          float gain_for_right;
          CalculateWeightsAndGains(grad_for_right, hess_for_right, l1, l2,
                                   &contrib_for_right, &gain_for_right);
          if (GainIsLarger(gain_for_left + gain_for_right, best_gain)) {
            best_gain = gain_for_left + gain_for_right;
            best_bucket = bucket;
            best_f_dim = f_dim;
            // TODO(crawles): multi-class support.
            best_contrib_for_left = contrib_for_left[0];
            best_contrib_for_right = contrib_for_right[0];
          }
        }  // for bucket
      }    // for f_dim
      if (best_gain == std::numeric_limits<float>::lowest()) {
        // Do not add the node if not split if found.
        continue;
      }
      output_node_ids.push_back(node_id);
      // Remove the parent gain for the parent node.
      output_gains.push_back(best_gain - parent_gain);
      output_feature_dimensions.push_back(best_f_dim);
      // default direction is fixed for dense splits.
      // TODO(tanzheny) account for default values.
      output_split_types.push_back(best_split_type);
      output_thresholds.push_back(best_bucket);
      output_left_node_contribs.push_back(best_contrib_for_left);
      output_right_node_contribs.push_back(best_contrib_for_right);
    }  // for node id
    const int num_nodes = output_node_ids.size();
    // output_node_ids
    Tensor* output_node_ids_t = nullptr;
    OP_REQUIRES_OK(context, context->allocate_output("node_ids", {num_nodes},
                                                     &output_node_ids_t));
    auto output_node_ids_vec = output_node_ids_t->vec<int32>();

    // output_gains
    Tensor* output_gains_t;
    OP_REQUIRES_OK(context, context->allocate_output("gains", {num_nodes},
                                                     &output_gains_t));
    auto output_gains_vec = output_gains_t->vec<float>();

    // output_feature_dimensions
    Tensor* output_feature_dimension_t;
    OP_REQUIRES_OK(context,
                   context->allocate_output("feature_dimensions", {num_nodes},
                                            &output_feature_dimension_t));
    auto output_feature_dimensions_vec =
        output_feature_dimension_t->vec<int32>();

    // output_thresholds
    Tensor* output_thresholds_t;
    OP_REQUIRES_OK(context, context->allocate_output("thresholds", {num_nodes},
                                                     &output_thresholds_t));
    auto output_thresholds_vec = output_thresholds_t->vec<int32>();

    // output_left_node_contribs
    Tensor* output_left_node_contribs_t;
    // TODO(crawles): Using logits_dim_ for multi-class split.
    OP_REQUIRES_OK(
        context, context->allocate_output("left_node_contribs", {num_nodes, 1},
                                          &output_left_node_contribs_t));
    auto output_left_node_contribs_matrix =
        output_left_node_contribs_t->matrix<float>();

    // output_right_node_contribs
    Tensor* output_right_node_contribs_t;
    OP_REQUIRES_OK(
        context, context->allocate_output("right_node_contribs", {num_nodes, 1},
                                          &output_right_node_contribs_t));
    auto output_right_node_contribs_matrix =
        output_right_node_contribs_t->matrix<float>();

    // split type
    Tensor* output_split_types_t;
    OP_REQUIRES_OK(
        context, context->allocate_output("split_with_default_directions",
                                          {num_nodes}, &output_split_types_t));
    auto output_split_types_vec = output_split_types_t->vec<string>();

    // Sets output tensors from vectors.
    for (int i = 0; i < num_nodes; ++i) {
      output_node_ids_vec(i) = output_node_ids[i];
      // Adjust the gains to penalize by tree complexity.
      output_gains_vec(i) = output_gains[i] - tree_complexity;
      output_feature_dimensions_vec(i) = output_feature_dimensions[i];
      output_thresholds_vec(i) = output_thresholds[i];
      output_left_node_contribs_matrix(i, 0) = output_left_node_contribs[i];
      output_right_node_contribs_matrix(i, 0) = output_right_node_contribs[i];
      output_split_types_vec(i) = output_split_types[i];
    }
  }

 private:
  int logits_dim_;
};

// v2 op that supports multi-class.
REGISTER_KERNEL_BUILDER(
    Name("BoostedTreesCalculateBestFeatureSplit").Device(DEVICE_CPU),
    BoostedTreesCalculateBestFeatureSplitOp);

class BoostedTreesMakeStatsSummaryOp : public OpKernel {
 public:
  explicit BoostedTreesMakeStatsSummaryOp(OpKernelConstruction* const context)
      : OpKernel(context) {
    OP_REQUIRES_OK(context, context->GetAttr("max_splits", &max_splits_));
    OP_REQUIRES_OK(context, context->GetAttr("num_buckets", &num_buckets_));
    OP_REQUIRES_OK(context, context->GetAttr("num_features", &num_features_));
  }

  void Compute(OpKernelContext* const context) override {
    // node_ids
    const Tensor* node_ids_t;
    OP_REQUIRES_OK(context, context->input("node_ids", &node_ids_t));
    const auto node_ids = node_ids_t->vec<int32>();
    // gradients
    const Tensor* gradients_t;
    OP_REQUIRES_OK(context, context->input("gradients", &gradients_t));
    const auto gradients = gradients_t->matrix<float>();
    // hessians
    const Tensor* hessians_t;
    OP_REQUIRES_OK(context, context->input("hessians", &hessians_t));
    const auto hessians = hessians_t->matrix<float>();
    // bucketized_features
    OpInputList bucketized_features_list;
    OP_REQUIRES_OK(context, context->input_list("bucketized_features_list",
                                                &bucketized_features_list));
    // Infer batch size.
    const int64 batch_size = node_ids_t->dim_size(0);

    // Allocate temporary stats tensor (Rank 4).
    Tensor temp_stats_double_t;
    OP_REQUIRES_OK(context, context->allocate_temp(
                                DT_DOUBLE,
                                {num_features_, max_splits_, num_buckets_, 2},
                                &temp_stats_double_t));
    auto temp_stats_double = temp_stats_double_t.tensor<double, 4>();
    temp_stats_double.setZero();

    // Partition by node, and then bucketize.
    for (int feature_idx = 0; feature_idx < num_features_; ++feature_idx) {
      const auto& features = bucketized_features_list[feature_idx].vec<int32>();
      for (int i = 0; i < batch_size; ++i) {
        const int32 node = node_ids(i);
        const int32 bucket = features(i);
        temp_stats_double(feature_idx, node, bucket, 0) += gradients(i, 0);
        temp_stats_double(feature_idx, node, bucket, 1) += hessians(i, 0);
      }
    }

    // Copy temp tensor over to output tensor.
    Tensor* output_stats_summary_t = nullptr;
    OP_REQUIRES_OK(context, context->allocate_output(
                                "stats_summary", temp_stats_double_t.shape(),
                                &output_stats_summary_t));
    output_stats_summary_t->tensor<float, 4>() =
        temp_stats_double.template cast<float>();
  }

 private:
  int max_splits_;
  int num_buckets_;
  int num_features_;
};

REGISTER_KERNEL_BUILDER(Name("BoostedTreesMakeStatsSummary").Device(DEVICE_CPU),
                        BoostedTreesMakeStatsSummaryOp);

class BoostedTreesAggregateStatsOp : public OpKernel {
 public:
  explicit BoostedTreesAggregateStatsOp(OpKernelConstruction* const context)
      : OpKernel(context) {
    OP_REQUIRES_OK(context, context->GetAttr("max_splits", &max_splits_));
    OP_REQUIRES_OK(context, context->GetAttr("num_buckets", &num_buckets_));
  }

  void Compute(OpKernelContext* const context) override {
    // node_ids.
    const Tensor* node_ids_t;
    OP_REQUIRES_OK(context, context->input("node_ids", &node_ids_t));
    const auto node_ids = node_ids_t->vec<int32>();

    // gradients.
    const Tensor* gradients_t;
    OP_REQUIRES_OK(context, context->input("gradients", &gradients_t));
    const auto gradients = gradients_t->matrix<float>();

    // hessians.
    const Tensor* hessians_t;
    OP_REQUIRES_OK(context, context->input("hessians", &hessians_t));
    const auto hessians = hessians_t->matrix<float>();

    // feature.
    const Tensor* feature_t;
    OP_REQUIRES_OK(context, context->input("feature", &feature_t));
    const auto feature = feature_t->matrix<int32>();

    // Infer batch size, feature dimension and stats dimension.
    const int64 batch_size = node_ids_t->dim_size(0);
    const int64 logits_dims = gradients_t->dim_size(1);
    const int64 hessians_dims = hessians_t->dim_size(1);
    const int64 stats_dims = logits_dims + hessians_dims;
    const int64 feature_dims = feature_t->dim_size(1);

    // Allocate temporary stats tensor (Rank 4), upcasting to double.
    Tensor temp_stats_double_t;
    OP_REQUIRES_OK(context, context->allocate_temp(DT_DOUBLE,
                                                   {max_splits_, feature_dims,
                                                    num_buckets_, stats_dims},
                                                   &temp_stats_double_t));
    auto temp_stats_double = temp_stats_double_t.tensor<double, 4>();
    temp_stats_double.setZero();

    for (int i = 0; i < batch_size; ++i) {
      const int32 node = node_ids(i);
      for (int feature_dim = 0; feature_dim < feature_dims; ++feature_dim) {
        const int32 bucket = feature(i, feature_dim);
        for (int stat_dim = 0; stat_dim < logits_dims; ++stat_dim) {
          temp_stats_double(node, feature_dim, bucket, stat_dim) +=
              gradients(i, stat_dim);
        }
        for (int stat_dim = logits_dims; stat_dim < stats_dims; ++stat_dim) {
          temp_stats_double(node, feature_dim, bucket, stat_dim) +=
              hessians(i, stat_dim - logits_dims);
        }
      }
    }

    // Copy temp tensor over to output tensor, downcasting to float.
    Tensor* output_stats_summary_t = nullptr;
    OP_REQUIRES_OK(context, context->allocate_output(
                                "stats_summary", temp_stats_double_t.shape(),
                                &output_stats_summary_t));
    output_stats_summary_t->tensor<float, 4>() =
        temp_stats_double.template cast<float>();
  }

 private:
  int max_splits_;
  int num_buckets_;
};

REGISTER_KERNEL_BUILDER(Name("BoostedTreesAggregateStats").Device(DEVICE_CPU),
                        BoostedTreesAggregateStatsOp);

}  // namespace tensorflow
