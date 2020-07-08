所谓的优化，就是对这些instruction和computation按照某种优化算法进行操作变换，读源码的时候核心需要注意的也就是3点：

+ 优化算法
+ 指令
+ 指令支持的操作

指令就算是[操作语义](https://www.tensorflow.org/xla/operation_semantics#while)

其次是指令的操作：分别定义在hlo_instruction.h和hlo_computation.h中

过程：

在tensorflow/compiler/xla/service/cpu/BUILD第128行添加

``` h
126     "//tensorflow/compiler/xla/service:hlo_cse",
127     "//tensorflow/compiler/xla/service:hlo_dce",
128     "//tensorflow/compiler/xla/service:rouding",
129     "//tensorflow/compiler/xla/service:hlo_element_type_converter",
130     "//tensorflow/compiler/xla/service:hlo_ordering",
```

在tensorflow/compiler/xla/service/cpu/cpu_compiler.cc第110行中添加

``` h
108  #include "tensorflow/compiler/xla/service/while_loop_simplifier.h"
109  #include "tensorflow/compiler/xla/service/zero_sized_hlo_elimination.h"
110  #include "tensorflow/compiler/xla/service/rounding.h"
111  #include "tensorflow/compiler/xla/status_macros.h"
112  #include "tensorflow/compiler/xla/statusor.h"
```
在tensorflow/compiler/xla/service/cpu/cpu_compiler.cc第406行中添加

``` h
402  pipeline.AddPass<HloDCE>();
403  pipeline.AddPass<FlattenCallGraph>();
404  pipeline.AddPass<CpuCopyInsertion>();
405  pipeline.AddPass<HloDCE>();
406  pipeline.AddPass<rounding>();
```

在tensorflow/compiler/xla/service下添加rounding.h 

``` c
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
```

这个pass是整个优化pipeline中的一个pass，所以继承了HloModulePass并实现Run方法。

这个Run方法是所有pass必须实现的接口：

> `StatusOr<bool> Run(HloModule* module) override;`

返回值表示是否对module做了修改。

添加rounding.cc 

```cpp
#include "tensorflow/compiler/xla/service/Multi_simplification.h"

#include <list>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "absl/container/flat_hash_set.h"
#include "absl/container/inlined_vector.h"
#include "tensorflow/compiler/xla/layout_util.h"
#include "tensorflow/compiler/xla/literal.h"
#include "tensorflow/compiler/xla/service/hlo_computation.h"
#include "tensorflow/compiler/xla/service/hlo_domain_map.h"
#include "tensorflow/compiler/xla/service/hlo_instruction.h"
#include "tensorflow/compiler/xla/service/hlo_opcode.h"
#include "tensorflow/compiler/xla/service/rounding.h"
#include "tensorflow/compiler/xla/shape_util.h"
#include "tensorflow/compiler/xla/types.h"
#include "tensorflow/core/lib/core/errors.h"
#include "tensorflow/core/lib/hash/hash.h"

namespace xla {


namespace {
// 找到乘法计算，进行四舍五入   

StatusOr<bool> rounding::Run(HloModule* module){
  bool changed = false;

  VLOG(2) << "Before rouding_simplification:";
  XLA_VLOG_LINES(2, module->ToString());

  for(auto* computation : module->MakeComputationPostOrder()){

    for(auto* instruction : computation->instructions()){
      if (instruction != computation->root_instruction() &&
          instruction == add{
          
            changed = true;
      }
    }

  }
    
}

VLOG(2) << "After rouding_simplification:";
return changed;

}
}
```

第二次编译报错
![](https://github.com/dongbeiyewu/xla/raw/master/week12/pic/2.png)

更新

在D:\Users\sunqiming\tensorflow\tensorflow-r1.14_add_pass\tensorflow\compiler\xla\service\BUILD第3021行添加

```h
cc_library(
    name = "rounding",
    srcs = ["rounding.cc"],
    hdrs = ["rounding.h"],
    deps = [
        ":hlo",
        "//tensorflow/core:lib",
    ],
)

tf_cc_test(
    name = "rounding_test",
    srcs = ["rounding_test.cc"],
    deps = [
        ":tuple_util",
        "//tensorflow/compiler/xla:test",
        "//tensorflow/compiler/xla/service:hlo_matchers",
        "//tensorflow/compiler/xla/tests:xla_internal_test_main",
        "//tensorflow/compiler/xla/tools/parser:hlo_parser",
    ],
)
```
重新编译

![](https://github.com/dongbeiyewu/xla/raw/master/week12/pic/3.png)

在D:\Users\sunqiming\tensorflow\tensorflow-r1.14_add_pass\tensorflow\compiler\xla\service\cpu\BUILD第174行添加
```h
cc_library(
    name = "rounding",
    srcs = ["rounding.cc"],
    hdrs = ["rounding.h"],
    deps = [
        ":hlo",
        ":hlo_domain_map",
        ":hlo_pass",
        "//tensorflow/compiler/xla:literal",
        "//tensorflow/compiler/xla:shape_util",
        "//tensorflow/compiler/xla:types",
        "//tensorflow/core:lib",
        "@com_google_absl//absl/container:flat_hash_set",
        "@com_google_absl//absl/container:inlined_vector",
    ],
)

tf_cc_test(
    name = "rounding_test",
    srcs = ["rounding_test.cc"],
    deps = [
        ":cpu_plugin",
        ":hlo",
        ":hlo_cse",
        ":hlo_matchers",
        ":hlo_parser",
        "//tensorflow/compiler/xla:literal",
        "//tensorflow/compiler/xla:shape_util",
        "//tensorflow/compiler/xla:types",
        "//tensorflow/compiler/xla:util",
        "//tensorflow/compiler/xla:xla_data_proto",
        "//tensorflow/compiler/xla/tests:hlo_test_base",
        "//tensorflow/compiler/xla/tests:literal_test_util",
        "//tensorflow/compiler/xla/tests:test_utils",
        "//tensorflow/core:lib",
        "@com_google_absl//absl/memory",
    ],
)
```
重新编译

