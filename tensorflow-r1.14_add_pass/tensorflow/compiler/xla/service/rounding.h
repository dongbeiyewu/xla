
#ifndef TENSORFLOW_COMPILER_XLA_SERVICE_ROUNDING_H_
#define TENSORFLOW_COMPILER_XLA_SERVICE_ROUNDING_H_

#include "tensorflow/compiler/xla/service/hlo_computation.h"
#include "tensorflow/compiler/xla/service/hlo_instruction.h"
#include "tensorflow/compiler/xla/service/hlo_module.h"
#include "tensorflow/compiler/xla/service/hlo_pass_interface.h"

namespace xla{
    //将两个浮点数的乘积自动四舍五入为整数
    class rounding : public  HloModulePass{
    public:
     ~rounding()override{}
     absl::string_view name() const override {return "rouding"; }

    // 对于给定模块运行浮点数乘法简化，返回模块是否已经更改（浮点数乘法四舍五入）
   
    StatusOr<bool> Run(HloModule* module)override;
    
    };


     
}

   // namespace xla

#endif  // TENSORFLOW_COMPILER_XLA_SERVICE_ROUNDING_H_