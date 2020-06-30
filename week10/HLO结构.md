

+ hlo module用源码注释的解释，就是一个编译单元，相当于是一个完整可运行的程序。
+ 既然是一个程序，就有入口函数，也就是 entry_computation，每个module都有且仅有一个entry_computation，相当于main函数，有输入和输出，输入可以是多个参数，但输出只有一个（root instruction的值），如果要返回多个值，需要把多个值构造成一个元组（tuple）返回。
+ 一个module可以包含多个computation，除了entry_computation，其他的都是"nested"，也就是被调用。
+ HLO instructions就是op了，对应了官网上列出的operation semantics，看注释已经解释的非常清楚了，op融合和向llvm ir转换都是在这个层面进行的。


+ HLO指令是高级编译器IR的原子单元
+ hlo指令位于hlocompulation内部，它类似于其他编程语言中的函数。节点在其计算中没有总顺序。相反，它们具有由数据和控件依赖关系确定的部分顺序。
+ HLO没有基本块或显式的“分支”指令。相反，某些hlo指令——即kWhile、kConditional和kCall——对控制流进行编码。例如，kConditionalHLO根据谓词的运行时值执行两种可能的计算之一。

经过tf2xla后，得到的是一个hlo的图,tf2xla目录是调用了xla目录里的函数，xla目录内的结构是client/service结构的，service是真正的定义和调用发生处，client是对service的封装，xla_builder就在client内。而hlo的定义都在service内，在tf2xla中可以看到两个概念：xla_expression和xla_resource。

+ xla_expression 可以表示常量、符号值、变量和列表，但我理解最重要的是符号值的表示
+ xla_resource 表示hlo中的变量，这个不是很好理解，我理解如果xla只是作为推理用，那么hlo中除了输入参数，其他的参数都会转换成常量吧，这个变量应该是表示需要更新的数据，所以可能是表示梯度？用于训练？当然还有一个用途就是表示最终的输出值，这个毫无疑问，或者需要嵌套computation的时候，表示被嵌套computation的输出，被其他computation引用。

通过xla的client（compile_onlyclient）最终调用到cpu_compiler或gpu_compiler。