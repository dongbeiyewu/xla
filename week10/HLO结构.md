

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



![](https://github.com/dongbeiyewu/xla/raw/master/week10/pic/1.jpg)

+ 数据流图转换器用于将TensorFlow数据流图转换为XLA图，其源代码位于tensorflow/compiler/tf2xla。该模块定义了名为XlaOpKernel的XLA层函数类型，并且实现了一系列线性代数相关的核函数子类。在数据流图执行前，转换器基于数据流图的结构创建XLA图，将原图中那些可通过XLA优化的操作节点替换为XLA层函数。由于数据流图操作同HLO IR存在语义差异，节点的替换可能是一对一的，也可能是一对多的。
+ XLA编译器用于将XLA图编译为二进制文件，其源代码位于tensorflow/compile/xla。该模块基于LLVM技术实现，目前支持的后端设备包括x86 CPU、arm CPU和NV GPU。在编译器中，前端解析并优化XLA图，将其底层化为LLVM IR，后端将LLVM IR编译为设备特征的二进制文件，并执行二进制代码优化。XLA编译器采用客户端-服务器模式设计，以便管理编译过程的生命周期。HLO的实现亦位于该模块。
+ JIT编译机制用于在Tensorflow应用运行时创建执行数据流图操作的二进制代码，它是一套面向异构设备的通用性能优化机制。该模块的源代码位于tensorflow/compiler/jit。模型开发者使用JIT机制时，需要在代码中显式引入JIT API，将会话或操作配置为XLA编译模式。JIT编译机制会对数据流图的可优化部分实施优化，将其中大量细粒度的操作融合为少量粗粒度的专用核函数。这些核函数经由XLA编译器生成高效的二进制代码，能够减少图执行过程中内存分配和上下文切换开销

+ AOT编译机制用于在Tensorflow应用运行前创建集成了模型和运行时的二进制代码，主要适用于手机等移动端的推理性能优化。该代码位于tensorflow/compiler/aot。模型开发者使用AOT机制时，需要提供protocol buffers格式编写的数据流图定义文件及编译配置文件。然后使用bazel构建工具，调用AOT封装工具-tfcompile编译数据流图。AOT编译机制生成的二进制代码包含了模型和必要的运行时逻辑，不再需要完整的Tensorflow的运行时库支持，因此能够减小可执行程序的体积，一并生成的c++文件用于在应用代码中访问模型。
  
并非所有TensorFlow 运算都可由 XLA 编译，如果模型中有XLA 不支持的运算，XLA 编译就会失败。例如，XLA 不支持 tf.where 运算，因此如果您的模型函数包含此运算，使用 xla.compile 运行模型时便会失败。XLA 支持的每项 TensorFlow 运算都可在tensorflow/compiler/tf2xla/kernels/ 中调用 REGISTER_XLA_OP，因此您可以使用 grep 来搜索 REGISTER_XLA_OP 宏实例，以查找支持的 TensorFlow 运算列表。