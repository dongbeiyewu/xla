; ModuleID = 'cluster_6078579234811709973__.9'
source_filename = "cluster_6078579234811709973__.9"
target datalayout = "e-i64:64-i128:128-v16:16-v32:32-n16:32:64"
target triple = "nvptx64-nvidia-cuda"

define void @multiply_5(i8* align 64 dereferenceable(4) %alloc0, i8* align 16 dereferenceable(4) %alloc1, i8* align 16 dereferenceable(4) %alloc2) {
entry:
  %multiply.5.raw = getelementptr inbounds i8, i8* %alloc0, i64 0
  %multiply.5.typed = bitcast i8* %multiply.5.raw to float*
  %arg0.1.raw = getelementptr inbounds i8, i8* %alloc1, i64 0
  %arg0.1.typed = bitcast i8* %arg0.1.raw to float*
  %arg1.2.raw = getelementptr inbounds i8, i8* %alloc2, i64 0
  %arg1.2.typed = bitcast i8* %arg1.2.raw to float*
  %0 = call i32 @llvm.nvvm.read.ptx.sreg.ctaid.x(), !range !2
  %1 = call i32 @llvm.nvvm.read.ptx.sreg.tid.x(), !range !2
  %2 = mul nuw nsw i32 %0, 1
  %linear_index = add nuw nsw i32 %2, %1
  %linear_index_in_range = icmp ult i32 %linear_index, 1
  call void @llvm.assume(i1 %linear_index_in_range)
  %3 = icmp ult i32 %linear_index, 1
  br i1 %3, label %multiply.5.in_bounds-true, label %multiply.5.in_bounds-after

multiply.5.in_bounds-after:                       ; preds = %multiply.5.in_bounds-true, %entry
  ret void

multiply.5.in_bounds-true:                        ; preds = %entry
  %4 = load float, float* %arg0.1.typed, !invariant.load !3, !noalias !4
  %5 = load float, float* %arg1.2.typed, !invariant.load !3, !noalias !4
  %6 = fmul float %4, %5
  store float %6, float* %multiply.5.typed, !alias.scope !4
  br label %multiply.5.in_bounds-after
}

; Function Attrs: nounwind readnone
declare i32 @llvm.nvvm.read.ptx.sreg.ctaid.x() #0

; Function Attrs: nounwind readnone
declare i32 @llvm.nvvm.read.ptx.sreg.tid.x() #0

; Function Attrs: nounwind
declare void @llvm.assume(i1) #1

attributes #0 = { nounwind readnone }
attributes #1 = { nounwind }

!nvvm.annotations = !{!0, !1}

!0 = !{void (i8*, i8*, i8*)* @multiply_5, !"kernel", i32 1}
!1 = !{void (i8*, i8*, i8*)* @multiply_5, !"reqntidx", i32 1}
!2 = !{i32 0, i32 1}
!3 = !{}
!4 = !{!5}
!5 = !{!"buffer: {index:0, offset:0, size:4}", !6}
!6 = !{!"XLA global AA domain"}
