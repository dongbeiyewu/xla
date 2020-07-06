; ModuleID = 'cluster_6078579234811709973__.9'
source_filename = "cluster_6078579234811709973__.9"
target datalayout = "e-i64:64-i128:128-v16:16-v32:32-n16:32:64"
target triple = "nvptx64-nvidia-cuda"

; Function Attrs: norecurse nounwind
define void @multiply_5(i8* nocapture align 64 dereferenceable(4) %alloc0, i8* nocapture readonly align 16 dereferenceable(4) %alloc1, i8* nocapture readonly align 16 dereferenceable(4) %alloc2) local_unnamed_addr #0 {
entry:
  %alloc25 = addrspacecast i8* %alloc2 to i8 addrspace(1)*
  %alloc13 = addrspacecast i8* %alloc1 to i8 addrspace(1)*
  %alloc01 = addrspacecast i8* %alloc0 to i8 addrspace(1)*
  %arg1.2.typed = bitcast i8 addrspace(1)* %alloc25 to float addrspace(1)*
  %arg0.1.typed = bitcast i8 addrspace(1)* %alloc13 to float addrspace(1)*
  %multiply.5.typed = bitcast i8 addrspace(1)* %alloc01 to float addrspace(1)*
  %0 = load float, float addrspace(1)* %arg0.1.typed, align 16, !invariant.load !3, !noalias !4
  %1 = load float, float addrspace(1)* %arg1.2.typed, align 16, !invariant.load !3, !noalias !4
  %2 = fmul float %0, %1
  store float %2, float addrspace(1)* %multiply.5.typed, align 64, !alias.scope !4
  ret void
}

attributes #0 = { norecurse nounwind }

!nvvm.annotations = !{!0, !1}
!llvm.module.flags = !{!2}

!0 = !{void (i8*, i8*, i8*)* @multiply_5, !"kernel", i32 1}
!1 = !{void (i8*, i8*, i8*)* @multiply_5, !"reqntidx", i32 1}
!2 = !{i32 4, !"nvvm-reflect-ftz", i32 0}
!3 = !{}
!4 = !{!5}
!5 = !{!"buffer: {index:0, offset:0, size:4}", !6}
!6 = !{!"XLA global AA domain"}
