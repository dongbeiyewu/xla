## HLO->llvm优化过程
经过tf2xla后，得到的是一个hlo的图
以xla.CpuComiler为例
xla.CpuComiler的输入是xla.HloModule，首先会调用RunHloPasses创建HloPassPipeline，添加并运行一系列的HloPass.
## hlo module的概念
+ hlo module用源码注释的解释，就是一个编译单元，相当于是一个完整可运行的程序。
+ 一个程序，就有入口函数，也就是 entry_computation，每个module都有且仅有一个entry_computation，相当于main函数，有输入和输出，输入可以是多个参数，但输出只有一个（root instruction的值），如果要返回多个值，需要把多个值构造成一个元组（tuple）返回。
+ 一个module可以包含多个computation，除了entry_computation，其他的都是"nested"，也就是被调用。
+ HLO instructions就是op了，对应了官网上列出的operation semantics，看注释已经解释的非常清楚了，op融合和向llvm ir转换都是在这个层面进行的。
## module例子
hlo.proto里的一个例子
```
ENTRY main {
     a = f32[] parameter(0)
     b = f32[10] parameter(1)
     ROOT root = (f32[], f32[10]) tuple(%a, %b)
   }
```
一个简单的直接返回输入的图，用hlo表达就是上面这个样子，有输入参数，有root指令，有entry_computation，然后这整个就是一个module

tf2xla目录是调用了xla目录里的函数，xla目录内的结构本身又是client/service结构的，service是真正的定义和调用发生处，client是对service的封装，xla_builder就在client内。而hlo的定义都在service内

tf2xla中还可以看到两个概念：xla_expression和xla_resource

+ xla_expression 可以表示常量、符号值、变量和列表。
+ xla_resource 表示hlo中的变量，需要嵌套computation的时候，表示被嵌套computation的输出，被其他computation引用。

## high level optimize
通过xla的client（compile_onlyclient）最终调用到cpu_compiler或gpu_compiler。

如何做优化，结构很简单，就是通过一个pipeline（HloPassPipeline）把所有的pass串起来。

完成了各种pass优化和变换后，就是hlo_dataflow_analysis，最后是buffer_assignment。

所以整个流程就是

graph compile -> hlo graph build -> hlo pass pipelime -> hlo dataflow analysis -> codegen