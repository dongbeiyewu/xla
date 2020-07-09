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

#include "tensorflow/compiler/xla/service/gpu/nvptx_compiler.h"

#include <stdlib.h>

#include <atomic>
#include <functional>
#include <mutex>  // NOLINT(build/c++11): only using std::call_once, not mutex.
#include <utility>

#include "absl/memory/memory.h"
#include "absl/strings/numbers.h"
#include "absl/strings/str_cat.h"
#include "llvm/IR/DiagnosticInfo.h"
#include "llvm/IR/DiagnosticPrinter.h"
#include "llvm/IR/LLVMContext.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/Verifier.h"
#include "tensorflow/compiler/xla/protobuf_util.h"
#include "tensorflow/compiler/xla/service/algebraic_simplifier.h"
#include "tensorflow/compiler/xla/service/batchnorm_expander.h"
#include "tensorflow/compiler/xla/service/buffer_assignment.h"
#include "tensorflow/compiler/xla/service/buffer_liveness.h"
#include "tensorflow/compiler/xla/service/call_inliner.h"
#include "tensorflow/compiler/xla/service/conditional_simplifier.h"
#include "tensorflow/compiler/xla/service/convolution_group_converter.h"
#include "tensorflow/compiler/xla/service/dot_decomposer.h"
#include "tensorflow/compiler/xla/service/dump.h"
#include "tensorflow/compiler/xla/service/dynamic_index_splitter.h"
#include "tensorflow/compiler/xla/service/flatten_call_graph.h"
#include "tensorflow/compiler/xla/service/gpu/cudnn_batchnorm_rewriter.h"
#include "tensorflow/compiler/xla/service/gpu/cudnn_conv_algorithm_picker.h"
#include "tensorflow/compiler/xla/service/gpu/cudnn_conv_pad_for_tensor_cores.h"
#include "tensorflow/compiler/xla/service/gpu/cudnn_conv_padding_legalization.h"
#include "tensorflow/compiler/xla/service/gpu/cudnn_conv_rewriter.h"
#include "tensorflow/compiler/xla/service/gpu/cudnn_fused_conv_rewriter.h"
#include "tensorflow/compiler/xla/service/gpu/cusolver_rewriter.h"
#include "tensorflow/compiler/xla/service/gpu/fusion_merger.h"
#include "tensorflow/compiler/xla/service/gpu/gpu_constants.h"
#include "tensorflow/compiler/xla/service/gpu/gpu_copy_insertion.h"
#include "tensorflow/compiler/xla/service/gpu/gpu_executable.h"
#include "tensorflow/compiler/xla/service/gpu/gpu_hlo_schedule.h"
#include "tensorflow/compiler/xla/service/gpu/gpu_hlo_support_checker.h"
#include "tensorflow/compiler/xla/service/gpu/gpu_layout_assignment.h"
#include "tensorflow/compiler/xla/service/gpu/gpu_sanitize_constant_names.h"
#include "tensorflow/compiler/xla/service/gpu/instruction_fusion.h"
#include "tensorflow/compiler/xla/service/gpu/ir_emission_utils.h"
#include "tensorflow/compiler/xla/service/gpu/ir_emitter_context.h"
#include "tensorflow/compiler/xla/service/gpu/ir_emitter_unnested.h"
#include "tensorflow/compiler/xla/service/gpu/llvm_gpu_backend/nvptx_backend_lib.h"
#include "tensorflow/compiler/xla/service/gpu/multi_output_fusion.h"
#include "tensorflow/compiler/xla/service/gpu/partition_assignment.h"
#include "tensorflow/compiler/xla/service/gpu/stream_assignment.h"
#include "tensorflow/compiler/xla/service/gpu/stream_executor_util.h"
#include "tensorflow/compiler/xla/service/gpu/thunk_schedule.h"
#include "tensorflow/compiler/xla/service/gpu/variadic_op_splitter.h"
#include "tensorflow/compiler/xla/service/hlo.pb.h"
#include "tensorflow/compiler/xla/service/hlo_computation.h"
#include "tensorflow/compiler/xla/service/hlo_constant_folding.h"
#include "tensorflow/compiler/xla/service/hlo_cse.h"
#include "tensorflow/compiler/xla/service/hlo_dce.h"
#include "tensorflow/compiler/xla/service/hlo_element_type_converter.h"
#include "tensorflow/compiler/xla/service/hlo_get_dimension_size_rewriter.h"
#include "tensorflow/compiler/xla/service/hlo_instruction.h"
#include "tensorflow/compiler/xla/service/hlo_pass_fix.h"
#include "tensorflow/compiler/xla/service/hlo_pass_pipeline.h"
#include "tensorflow/compiler/xla/service/hlo_proto_util.h"
#include "tensorflow/compiler/xla/service/hlo_subcomputation_unification.h"
#include "tensorflow/compiler/xla/service/hlo_verifier.h"
#include "tensorflow/compiler/xla/service/llvm_ir/llvm_util.h"
#include "tensorflow/compiler/xla/service/reduce_precision_insertion.h"
#include "tensorflow/compiler/xla/service/reshape_mover.h"
#include "tensorflow/compiler/xla/service/slice_sinker.h"
#include "tensorflow/compiler/xla/service/sort_simplifier.h"
#include "tensorflow/compiler/xla/service/stable_sort_expander.h"
#include "tensorflow/compiler/xla/service/transpose_folding.h"
#include "tensorflow/compiler/xla/service/tuple_simplifier.h"
#include "tensorflow/compiler/xla/service/while_loop_constant_sinking.h"
#include "tensorflow/compiler/xla/service/while_loop_simplifier.h"
#include "tensorflow/compiler/xla/service/while_loop_trip_count_annotator.h"
#include "tensorflow/compiler/xla/service/zero_sized_hlo_elimination.h"
#include "tensorflow/compiler/xla/status_macros.h"
#include "tensorflow/compiler/xla/types.h"
#include "tensorflow/compiler/xla/util.h"
#include "tensorflow/core/lib/core/status.h"
#include "tensorflow/core/lib/gtl/cleanup.h"
#include "tensorflow/core/lib/io/path.h"
#include "tensorflow/core/platform/cuda_libdevice_path.h"
#include "tensorflow/core/platform/env.h"
#include "tensorflow/core/platform/logging.h"
#include "tensorflow/core/platform/regexp.h"
#include "tensorflow/core/platform/stream_executor_no_cuda.h"
#include "tensorflow/core/platform/subprocess.h"
#include "tensorflow/core/platform/tracing.h"
#include "tensorflow/core/profiler/lib/traceme.h"
#include "tensorflow/stream_executor/cuda/cuda_diagnostics.h"
#include "tensorflow/stream_executor/cuda/ptxas_utils.h"

namespace xla {
namespace gpu {

/* static */ const char* NVPTXCompiler::kTargetTriple = "nvptx64-nvidia-cuda";
/* static */ const char* NVPTXCompiler::kDataLayout =
    "e-i64:64-i128:128-v16:16-v32:32-n16:32:64";

namespace {

namespace tracing = tensorflow::tracing;

static std::vector<std::string> CandidateCudaRoots(
    const HloModuleConfig& config) {
  return tensorflow::CandidateCudaRoots(
      config.debug_options().xla_gpu_cuda_data_dir());
}

void PrintCantFindCudaMessage(absl::string_view msg,
                              const HloModuleConfig& hlo_module_config) {
  LOG(WARNING) << msg;
  LOG(WARNING) << "Searched for CUDA in the following directories:";

  for (const auto& dir : CandidateCudaRoots(hlo_module_config)) {
    LOG(WARNING) << "  " << dir;
  }
  LOG(WARNING)
      << "You can choose the search directory by setting xla_gpu_cuda_data_dir "
         "in HloModule's DebugOptions.  For most apps, setting the environment "
         "variable XLA_FLAGS=--xla_gpu_cuda_data_dir=/path/to/cuda will work.";
}

// Returns the directory containing nvvm libdevice files.
string GetLibdeviceDir(const HloModuleConfig& hlo_module_config) {
  for (const string& cuda_root : CandidateCudaRoots(hlo_module_config)) {
    string libdevice_dir =
        tensorflow::io::JoinPath(cuda_root, "nvvm", "libdevice");
    VLOG(2) << "Looking for libdevice at " << libdevice_dir;
    if (tensorflow::Env::Default()->IsDirectory(libdevice_dir).ok()) {
      VLOG(2) << "Found libdevice dir " << libdevice_dir;
      return libdevice_dir;
    }
  }
  PrintCantFindCudaMessage(
      "Can't find libdevice directory ${CUDA_DIR}/nvvm/libdevice. This may "
      "result in compilation or runtime failures, if the program we try to run "
      "uses routines from libdevice.",
      hlo_module_config);

  // GetCudaRotCandidates always inclues ".", but but if everything fails, we
  // return it anyway.  Better than returning the empty string.
  return ".";
}

// Runs optimization passes on the given HLO module.
//
// It takes a compiler pointer, as passes may compile and execute HLOs on the
// fly for cuDNN verification or other purposes.
Status OptimizeHloModule(HloModule* hlo_module, se::StreamExecutor* stream_exec,
                         se::DeviceMemoryAllocator* device_allocator,
                         Compiler* compiler) {
  {
    HloPassPipeline pipeline("optimization");
    pipeline.AddInvariantChecker<HloVerifier>(/*layout_sensitive=*/false,
                                              /*allow_mixed_precision=*/false);
    // Remove zero-sized HLO from the input so that other passes don't have to
    // handle it.
    pipeline.AddPass<ZeroSizedHloElimination>();

    pipeline.AddPass<DynamicIndexSplitter>();
    pipeline.AddPass<GpuHloSupportChecker>();
    ReducePrecisionInsertion::AddPasses(
        &pipeline, hlo_module->config().debug_options(),
        ReducePrecisionInsertion::PassTiming::BEFORE_OPTIMIZATION);

    // TODO(b/64094172): make Call work on GPU instead of inlining.
    pipeline.AddPass<CallInliner>();
    auto cost_model = [](HloInstruction* conv) {
      // We need a cost model for GPUs. Currently, do nothing.
      return false;
    };
    pipeline.AddPass<DotDecomposer>();
    pipeline.AddPass<ConvolutionGroupConverter>(
        cost_model,
        /*convert_batch_groups_only=*/true);
    // Expand the sort op to support stable sorting if required.
    pipeline.AddPass<StableSortExpander>();
    // Convert BF16 operations to F32 operations so that the GPU backend can
    // support BF16 operations without directly implementing a BF16 lowering for
    // most ops.
    pipeline.AddPass<HloElementTypeConverter>(BF16, F32);

    {
      auto& pass =
          pipeline.AddPass<HloPassFix<HloPassPipeline>>("simplification");
      pass.AddInvariantChecker<HloVerifier>(/*layout_sensitive=*/false,
                                            /*allow_mixed_precision=*/false);

      // If cudnn batchnorms are enabled, rewrite batchnorm HLOs to cudnn calls
      // where possible.  Not every batchnorm op can be implemented as a call to
      // cudnn, so decompose any remaining batchnorm ops into a soup of HLOs.
      if (hlo_module->config().debug_options().xla_gpu_use_cudnn_batchnorm()) {
        pass.AddPass<CudnnBatchNormRewriter>();
      }
      pass.AddPass<BatchNormExpander>(
          /*rewrite_training_op=*/true,
          /*rewrite_inference_op=*/true,
          /*rewrite_grad_op=*/true);

      pipeline.AddPass<HloGetDimensionSizeRewriter>();

      // BatchNormExpander can create zero-sized ops, so zero-sized HLO
      // elimination has to come after that pass.
      pipeline.AddPass<ZeroSizedHloElimination>();

      AlgebraicSimplifierOptions options;
      pass.AddPass<AlgebraicSimplifier>(options);
      pass.AddPass<SortSimplifier>();
      pass.AddPass<TupleSimplifier>();
      pass.AddPass<WhileLoopConstantSinking>();
      pass.AddPass<WhileLoopSimplifier>();
      pass.AddPass<SliceSinker>();
      pass.AddPass<HloDCE>();
      pass.AddPass<ReshapeMover>();
      pass.AddPass<HloConstantFolding>();
      pass.AddPass<ConditionalSimplifier>();
    }

    pipeline.AddPass<TransposeFolding>(
        [](const HloInstruction& dot,
           const TransposeFolding::OperandIndices& candidate_operands) {
          return ImplementedAsGemm(dot) ? candidate_operands
                                        : TransposeFolding::OperandIndices{};
        },
        TransposeFolding::NeverFoldTranspose);
    pipeline.AddPass<HloCSE>(/*is_layout_sensitive=*/false);
    pipeline.AddPass<HloDCE>();

    // Run WhileLoopTripCountAnnotator at the end of the simplification
    // pipeline, before layout assignment and fusion.  This pass does some
    // pattern-matching on while bodies/conditions, and this is where the HLO is
    // "nicest".
    //
    // It's important that we don't make semantic changes (e.g. unrolling) to
    // any `while` loops after this point, because otherwise the trip-count
    // annotations added by this pass may not be correct after the
    // modifications.
    pipeline.AddPass<WhileLoopTripCountAnnotator>();
    TF_RETURN_IF_ERROR(pipeline.Run(hlo_module).status());
  }

  {
    // Convert convolutions into CustomCalls to cudnn, then canonicalize them
    // (CudnnConvPaddingLegalization). Also expand cuSolver calls.
    HloPassPipeline pipeline("conv_canonicalization");
    pipeline.AddInvariantChecker<HloVerifier>(/*layout_sensitive=*/false,
                                              /*allow_mixed_precision=*/false);
    pipeline.AddPass<CusolverRewriter>(stream_exec, device_allocator);
    pipeline.AddPass<CudnnConvRewriter>();
    pipeline.AddPass<CudnnFusedConvRewriter>();
    pipeline.AddPass<CudnnConvPaddingLegalization>();
    if (IsVoltaOrLater(*stream_exec)) {
      pipeline.AddPass<CudnnConvPadForTensorCores>();
      // CudnnConvPadForTensorCores leaves behind unnecessary
      // tuple/get-tuple-element pairs that TupleSimplifier fixes.
      pipeline.AddPass<TupleSimplifier>();
    }
    // CudnnConvRewriter, CudnnConvPaddingLegalization and
    // CudnnConvPadForTensorCores may add instructions which can be simplified
    // by constant folding.
    pipeline.AddPass<HloConstantFolding>();
    TF_RETURN_IF_ERROR(pipeline.Run(hlo_module).status());
  }

  {
    // Run layout assignment in a separate pipeline from
    // "post-layout-assignment" because we want everything after layout
    // assignment to have a layout-sensitive invariant-checker, but
    // HloPassPipeline also runs its invariant checker before any passes are
    // run, meaning, the pipeline that contains layout assignment cannot contain
    // a layout-sensitive verifier!
    HloPassPipeline pipeline("layout assignment");
    pipeline.AddPass<GpuLayoutAssignment>(
        hlo_module->mutable_entry_computation_layout(),
        LayoutAssignment::InstructionCanChangeLayout, stream_exec);
    TF_RETURN_IF_ERROR(pipeline.Run(hlo_module).status());
  }

  {
    HloPassPipeline pipeline("post-layout_assignment");
    /* TODO(b/117531509): Use LayoutAssignment::InstructionCanChangeLayout after
     * fixing the ticket. */
    pipeline.AddInvariantChecker<HloVerifier>(
        /*layout_sensitive=*/true,
        /*allow_mixed_precision=*/false,
        LayoutAssignment::InstructionCanChangeLayout);

    // The LayoutAssignment pass may leave behind kCopy instructions which are
    // duplicate or NOPs, so remove them with algebraic simplification and CSE.
    AlgebraicSimplifierOptions options;
    options.set_is_layout_sensitive(true);
    pipeline.AddPass<HloPassFix<AlgebraicSimplifier>>(options);

    // Choose the fastest algorithm for each conv.
    //
    // We pick the algorithm before fusion so we can generate better HLO. After
    // CudnnConvRewriter, our convolutions are CustomCalls which return a
    // tuple (conv_result, scratch_memory), and the each conv uses 0 bytes of
    // scratch:
    //
    //   customcall = (f32[...], f32[0])
    //   return gte(customcall, 0)
    //
    // The algorithm picker then chooses the best algorithm, and potentially
    // increases the scratch space.  It replaces customcall with new_tuple,
    // giving us the following:
    //
    //   new_customcall = (f32[...], f32[N])
    //   new_tuple = tuple(gte(new_customcall, 0), constant f32[0])
    //   return gte(new_tuple, 0)
    //
    // The new tuple and gte instructions then be simplified away, because
    // nobody is expected to use the scratch value.
    //
    // However, if we were to run CudnnConvAlgorithmPicker after fusion
    // the gte(customcall, 0) would probably already be into a fusion node.  We
    // can't simplify across HloComputation boundaries, so in this case we
    // wouldn't be able to simplify away the new_tuple bits.
    pipeline.AddPass<CudnnConvAlgorithmPicker>(stream_exec, device_allocator,
                                               compiler);

    // Clean up new_tuple described above.
    pipeline.AddPass<TupleSimplifier>();

    pipeline.AddPass<HloCSE>(/*is_layout_sensitive=*/true);
    TF_RETURN_IF_ERROR(pipeline.Run(hlo_module).status());
  }

  {
    HloPassFix<HloPassPipeline> fusion("fusion");
    // We try to split variadic ops with many parameters into several such ops
    // to avoid exceeding the parameter space.
    fusion.AddPass<VariadicOpSplitter>();
    /* TODO(b/117531509): Use LayoutAssignment::InstructionCanChangeLayout after
     * fixing the ticket. */
    fusion.AddInvariantChecker<HloVerifier>(
        /*layout_sensitive=*/true,
        /*allow_mixed_precision=*/false,
        LayoutAssignment::InstructionCanChangeLayout);
    fusion.AddPass<GpuInstructionFusion>(/*may_duplicate=*/false);
    fusion.AddPass<GpuInstructionFusion>(/*may_duplicate=*/true);
    fusion.AddPass<FusionMerger>();
    fusion.AddPass<GpuMultiOutputFusion>();
    fusion.AddPass<HloCSE>(/*is_layout_sensitive=*/true,
                           /*only_fusion_computations=*/true);
    fusion.AddPass<HloDCE>();
    TF_RETURN_IF_ERROR(fusion.Run(hlo_module).status());

    HloPassPipeline reduce_pipeline("reduce-precision");
    /* TODO(b/117531509): Use LayoutAssignment::InstructionCanChangeLayout after
     * fixing the ticket. */
    reduce_pipeline.AddInvariantChecker<HloVerifier>(
        /*is_layout_sensitive=*/true, /*allow_mixed_precision=*/false,
        LayoutAssignment::InstructionCanChangeLayout);
    ReducePrecisionInsertion::AddPasses(
        &reduce_pipeline, hlo_module->config().debug_options(),
        ReducePrecisionInsertion::PassTiming::AFTER_FUSION);
    StatusOr<bool> reduce_result = reduce_pipeline.Run(hlo_module);
    TF_RETURN_IF_ERROR(reduce_result.status());

    if (reduce_result.ValueOrDie()) {
      // Do another fusion pass, with the expectation that we may be able to
      // fuse the new ReducePrecision operations.
      TF_RETURN_IF_ERROR(fusion.Run(hlo_module).status());
    }
  }

  return Status::OK();
}

// Modifies the given HLO module so that it will be accepted by IrEmitter.
// Unlike optimization passes, the passes are necessary for correctness.
Status PrepareHloModuleForIrEmitting(HloModule* hlo_module) {
  // In some cases, we have to place the result of an instruction in a temporary
  // buffer. For instance, the buffer that holds an external parameter is
  // assumed immutable at this point, and should not be reused for output
  // (b/27180329). Therefore, in that case, we set the output to be a copy of
  // the parameter.
  HloPassPipeline pipeline("GPU-ir-emit-prepare");
  /* TODO(b/117531509): Use LayoutAssignment::InstructionCanChangeLayout after
   * fixing the ticket. */
  pipeline.AddInvariantChecker<HloVerifier>(
      /*layout_sensitive=*/true,
      /*allow_mixed_precision=*/false,
      LayoutAssignment::InstructionCanChangeLayout);

  // Copy insertion should be performed immediately before IR emission to avoid
  // inserting unnecessary copies (later pass adds an instruction which
  // materializes the value) or missing a necessary copy (later pass removes an
  // instruction which materializes a value). DCE must be run immediately before
  // (and sometime after) copy insertion, to avoid dead code from interfering
  // with the rewrites.
  pipeline.AddPass<HloDCE>();
  pipeline.AddPass<FlattenCallGraph>();
  pipeline.AddPass<GpuCopyInsertion>();
  pipeline.AddPass<GpuSanitizeConstantNames>();
  return pipeline.Run(hlo_module).status();
}

// Prints a warning if the ptx->sass JIT in the driver has known bugs.
//
// Using such a driver only a problem if we fail to use ptxas to compile our ptx
// and have to use the driver instead, so you should only call this function if
// we're going to use the driver JIT.
//
// Only prints a warning the first time it's called.
void WarnIfBadDriverJITVersion() {
  static std::once_flag run_once;
  std::call_once(run_once, [] {
    auto version_or_status = se::cuda::Diagnostician::FindKernelDriverVersion();
    if (!version_or_status.ok()) {
      LOG(WARNING) << "Couldn't read CUDA driver version.";
      return;
    }
    se::cuda::DriverVersion version = version_or_status.ValueOrDie();

    // The following versions of the driver JIT miscompile some address
    // calculations with large offsets (e.g. "load ptr + large_constant"),
    // b/70245379:
    //
    //  - 384.x before 384.108
    //  - 387.x before 387.40
    //  - 390.x before 390.10.
    //
    // In addition, only >= 396.20 contains ptxas >= 9.2.88, which contains the
    // fix for the "large multioutput fusions" miscompile, b/111107644.
    if (version < std::make_tuple(396, 20, 0)) {
      LOG(WARNING)
          << "*** WARNING *** Invoking the PTX->SASS JIT from driver version "
          << se::cuda::DriverVersionToString(version)
          << ", which is older than 396.20.0. These versions are known to "
             "miscompile XLA code, leading to incorrect results or "
             "invalid-address errors.\nXLA only uses the driver JIT if it "
             "cannot find ptxas; you don't need to update your driver if "
             "you can point XLA to ptxas 9.2.88 or newer.";
    }
  });
}


}  // namespace

NVPTXCompiler::NVPTXCompiler()
    : pointer_size_(llvm::DataLayout(kDataLayout)
                        .getPointerSize(0 /* default address space */)) {}

StatusOr<std::unique_ptr<HloModule>> NVPTXCompiler::RunHloPasses(
    std::unique_ptr<HloModule> module, se::StreamExecutor* stream_exec,
    se::DeviceMemoryAllocator* device_allocator) {
  // We dump the post-optimization HLO in RunBackend so no need to dump it here.
  XLA_SCOPED_LOGGING_TIMER("NVPTXCompiler::RunHloPasses");
  tensorflow::profiler::TraceMe activity(
      [&] { return absl::StrCat("HLO Transforms:", module->name()); },
      tensorflow::profiler::TraceMeLevel::kInfo);
  TF_RETURN_IF_ERROR(
      OptimizeHloModule(module.get(), stream_exec, device_allocator, this));

  TF_RETURN_IF_ERROR(PrepareHloModuleForIrEmitting(module.get()));

  return std::move(module);
}

StatusOr<std::unique_ptr<Executable>> NVPTXCompiler::RunBackend(
    std::unique_ptr<HloModule> module, se::StreamExecutor* stream_exec,
    se::DeviceMemoryAllocator* device_allocator) {
  XLA_SCOPED_LOGGING_TIMER("NVPTXCompiler::RunBackend");

  TF_RET_CHECK(stream_exec != nullptr);

  llvm::LLVMContext llvm_context;
  std::string buffer;
  llvm::raw_string_ostream error(buffer);
  llvm::DiagnosticPrinterRawOStream printer(error);
  auto DiagnosticHandler = [](const llvm::DiagnosticInfo& diag_info,
                              void* Context) {
    auto printer = static_cast<llvm::DiagnosticPrinterRawOStream*>(Context);
    diag_info.print(*printer);
  };
  llvm_context.setDiagnosticHandlerCallBack(DiagnosticHandler, &printer);

  llvm::Module llvm_module(module->name().c_str(), llvm_context);
  // Set the target triple and the data layout.
  llvm_module.setTargetTriple(kTargetTriple);
  llvm_module.setDataLayout(kDataLayout);

  // Determine the HLO schedule, which is an ordering of HLO instructions.  This
  // is used by buffer assignment to enable buffer reuse, and the same ordering
  // must also be used to determine the thunk launch schedule.
  std::unique_ptr<StreamAssignment> stream_assignment = AssignStreams(*module);
  TF_ASSIGN_OR_RETURN(
      std::unique_ptr<GpuHloSchedule> hlo_schedule,
      GpuHloSchedule::Build(*module, *stream_assignment, pointer_size_));

  // Run buffer analysis on the HLO graph. This analysis figures out which
  // temporary buffers are required to run the computation.
  TF_ASSIGN_OR_RETURN(
      std::unique_ptr<BufferAssignment> buffer_assignment,
      BufferAssigner::Run(
          module.get(), hlo_schedule->ConsumeHloOrdering(),
          BufferSizeBytesFunction(),
          /*color_alignment=*/
          [](LogicalBuffer::Color) { return kXlaAllocatedBufferAlignBytes; },
          /*allow_input_output_aliasing=*/false,
          /*allocate_buffers_for_constants=*/true));
  DumpHloModuleIfEnabled(*module, *buffer_assignment, "after_optimizations");

  IrEmitterContext ir_emitter_context(
      module.get(), buffer_assignment.get(), stream_exec->platform(),
      &stream_exec->GetDeviceDescription(), &llvm_module);

  HloComputation* entry_computation = module->entry_computation();
  IrEmitterUnnested ir_emitter(module->config(), entry_computation,
                               &ir_emitter_context);

  TF_RETURN_IF_ERROR(ir_emitter.EmitConstantGlobals());

  {
    XLA_SCOPED_LOGGING_TIMER("NVPTXCompiler::RunBackend - IR emission");
    TF_RETURN_IF_ERROR(entry_computation->Accept(&ir_emitter));
  }

  if (user_pre_optimization_hook_) {
    user_pre_optimization_hook_(llvm_module);
  }
  string ir_module_string_before_opt;
  const bool embed_ir_in_executable =
      module->config().debug_options().xla_embed_ir_in_executable();
  if (embed_ir_in_executable) {
    ir_module_string_before_opt = llvm_ir::DumpModuleToString(llvm_module);
  }

  llvm_ir::DumpIrIfEnabled(*module, llvm_module, /*optimized=*/false);

  {
    XLA_SCOPED_LOGGING_TIMER(
        "NVPTXCompiler::RunBackend - Running LLVM verifier");

    std::string err;
    llvm::raw_string_ostream err_stream(err);

    // verifyModule() returns true if the module is broken.
    TF_RET_CHECK(!llvm::verifyModule(llvm_module, &err_stream))
        << "Invalid LLVM IR before optimizations:\n"
        << err_stream.str()
        << "\nThis probably indicates a bug in the HLO -> LLVM IR lowering. "
           "Rerun with --xla_dump_to to get the IR. ";
  }

  string libdevice_dir;
  {
    tensorflow::mutex_lock lock(mutex_);

    // Find the directory containing libdevice.  To avoid searching for it every
    // time, we have a one-element cache, keyed on the module's config's
    // cuda_data_dir.
    if (cached_libdevice_dir_.empty()) {
      cached_libdevice_dir_ = GetLibdeviceDir(module->config());
    }
    libdevice_dir = cached_libdevice_dir_;
  }
  VLOG(2) << "Libdevice dir = " << libdevice_dir << "\n";

  int cc_major, cc_minor;
  if (!stream_exec->GetDeviceDescription().cuda_compute_capability(&cc_major,
                                                                   &cc_minor)) {
    LOG(WARNING)
        << "Couldn't get compute capability for device; assuming sm_20.";
    cc_major = 2;
    cc_minor = 0;
  }

  string ptx;
  {
    XLA_SCOPED_LOGGING_TIMER("NVPTXCompiler::RunBackend - CompileToPtx");
    TF_ASSIGN_OR_RETURN(ptx, CompileToPtx(&llvm_module, {cc_major, cc_minor},
                                          module->config(), libdevice_dir));
  }

  llvm_ir::DumpIrIfEnabled(*module, llvm_module, /*optimized=*/true);

  if (user_post_optimization_hook_) {
    user_post_optimization_hook_(llvm_module);
  }
  // Write PTX to IR dump directory, if IR dumping was requested.
  if (DumpingEnabledForHloModule(*module)) {
    DumpToFileInDirOrStdout(*module, "ptx", ptx);
  }

  const std::vector<uint8> cubin = CompilePtxOrGetCachedResult(
      stream_exec, ptx, cc_major, cc_minor, module->config());

  auto thunk_schedule = absl::make_unique<ThunkSchedule>(
      ir_emitter.ConsumeThunkSequence(), std::move(stream_assignment),
      hlo_schedule->ThunkLaunchOrder());
  if (DumpingEnabledForHloModule(*module)) {
    DumpToFileInDirOrStdout(*module, "thunk_schedule",
                            thunk_schedule->ToString());
  }

  std::unique_ptr<HloProfileIndexMap> profile_index_map;
  std::unique_ptr<HloProfilePrinterData> profile_printer;

  if (module->config().hlo_profiling_enabled() || VLOG_IS_ON(1)) {
    HloCostAnalysis cost_analysis(ShapeSizeBytesFunction());
    cost_analysis.set_bytes_per_second(
        stream_exec->GetDeviceDescription().memory_bandwidth());
    TF_RETURN_IF_ERROR(module->entry_computation()->Accept(&cost_analysis));
    VLOG(1) << "HLO memory read+written: "
            << tensorflow::strings::HumanReadableNumBytes(
                   cost_analysis.bytes_accessed());
    if (module->config().hlo_profiling_enabled()) {
      profile_index_map = absl::make_unique<HloProfileIndexMap>(*module);
      profile_printer = CreateHloProfilePrinterData(
          *profile_index_map, cost_analysis, entry_computation->name());
    }
  }

  auto* gpu_executable = new GpuExecutable(
      ptx, cubin, {cc_major, cc_minor}, std::move(thunk_schedule),
      std::move(module), std::move(buffer_assignment),
      std::move(profile_printer), std::move(profile_index_map));
  if (embed_ir_in_executable) {
    DCHECK_NE("", ir_module_string_before_opt);
    gpu_executable->set_ir_module_string(ir_module_string_before_opt);
  }
  return std::unique_ptr<Executable>(gpu_executable);
}

std::vector<uint8> NVPTXCompiler::CompilePtxOrGetCachedResult(
    se::StreamExecutor* stream_exec, const string& ptx, int cc_major,
    int cc_minor, const HloModuleConfig& hlo_module_config) {
  XLA_SCOPED_LOGGING_TIMER("NVPTXCompiler::CompilePtxOrGetCachedResult");
  tensorflow::profiler::TraceMe activity(
      "PTX->CUBIN", tensorflow::profiler::TraceMeLevel::kInfo);
  bool inserted;
  decltype(compilation_cache_.begin()) iter;
  // Pointers into compilation_cache_ where the ptx and (optional) cubin are
  // stored.
  const string* cache_ptx = nullptr;
  CompilationCacheValue* cache_value = nullptr;

  {
    tensorflow::mutex_lock lock(mutex_);
    std::tie(iter, inserted) = compilation_cache_.emplace(
        std::piecewise_construct,
        std::forward_as_tuple(ptx, cc_major, cc_minor),
        std::forward_as_tuple());
    cache_ptx = &iter->first.ptx;
    cache_value = &iter->second;
  }

  // Compile the ptx if it wasn't in the cache before we called this function.
  // Other threads asking for the same compilation key will block on
  // cache_value->mutex_ until compilation is done.
  {
    tensorflow::mutex_lock lock(cache_value->mutex_);
    if (inserted) {
      CHECK(!cache_value->compilation_done);
      if (!ptx.empty()) {
        StatusOr<std::vector<uint8>> maybe_cubin = se::cuda::CompilePtx(
            stream_exec->device_ordinal(), cache_ptx->c_str(),
            PtxOptsFromConfig(hlo_module_config));
        if (maybe_cubin.ok()) {
          cache_value->cubin_data = std::move(maybe_cubin).ValueOrDie();
          VLOG(2) << "Compiled PTX size:" << ptx.size()
                  << " CUBIN size: " << cache_value->cubin_data.size();
        } else {
          bool log_warning = true;
          if (maybe_cubin.status().code() ==
              tensorflow::error::Code::NOT_FOUND) {
            // Missing ptxas is expected in some environments where CUDA SDK
            // binaries are not available. We don't want to spam logs with
            // identical warnings in this case.

            // TODO(jlebar): we should implement a LOG_FIRST_N and LOG_EVERY_N
            // for more general usage.
            static std::atomic<bool> warning_done(false);
            log_warning = !warning_done.exchange(true);
          }
          if (log_warning) {
            PrintCantFindCudaMessage(
                "Can't find ptxas binary in ${CUDA_DIR}/bin.  Will back to the "
                "GPU driver for PTX -> sass compilation.  This is OK so long "
                "as you don't see a warning below about an out-of-date driver "
                "version.",
                hlo_module_config);
          }

          // We're going to use the driver to JIT our PTX->SASS, so warn if
          // the JIT in the driver has known bugs.
          WarnIfBadDriverJITVersion();
        }
      }
      cache_value->compilation_done = true;
      cache_value->compilation_done_cv_.notify_all();
    } else {
      while (!cache_value->compilation_done) {
        cache_value->compilation_done_cv_.wait(lock);
      }
    }
  }

  CHECK(cache_value != nullptr);
  CHECK(cache_value->compilation_done);
  return cache_value->cubin_data;
}

StatusOr<std::vector<std::unique_ptr<AotCompilationResult>>>
NVPTXCompiler::CompileAheadOfTime(std::unique_ptr<HloModuleGroup> module_group,
                                  const AotCompilationOptions& options) {
  return Unimplemented(
      "not yet implemented: NVPTXCompiler::CompileAheadOfTime");
}

se::Platform::Id NVPTXCompiler::PlatformId() const {
  return se::cuda::kCudaPlatformId;
}

}  // namespace gpu
}  // namespace xla

static bool InitModule() {
  xla::Compiler::RegisterCompilerFactory(
      stream_executor::cuda::kCudaPlatformId,
      []() { return absl::make_unique<xla::gpu::NVPTXCompiler>(); });
  return true;
}
static bool module_initialized = InitModule();
