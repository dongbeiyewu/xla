
#ifndef TENSORFLOW_COMPILER_XLA_SERVICE_HLO_CSE_H_
#define TENSORFLOW_COMPILER_XLA_SERVICE_HLO_CSE_H_

#include "tensorflow/compiler/xla/service/hlo_module.h"
#include "tensorflow/compiler/xla/service/hlo_pass_interface.h"

namespace xla{
    //将两个浮点数的乘积自动四舍五入为整数
    class Multisimple : public  HloModulePass{
    public:
    //如果是浮点数,那么进行四舍五入
    //整数乘法则忽略
    explicit Multisimple(bool is_folat_mul,
                         bool only_fusion_computations = false)
        : is_folat_mul_(is_folat_mul),
          only_fusion_computations_(only_fusion_computations){}
    ~ Multisimple() override = default;


    // 对于给定模块运行浮点数乘法简化，返回模块是否已经更改（浮点数乘法四舍五入）
    StatusOr<bool> Run(HloModule* module) override;

    private:
    const bool is_folat_mul_;
    const bool only_fusion_computations_
};


}   // namespace xla