## LLVM

LLVM是一个编译器框架，由C++语言编写而成，包括一系列分模块、可重用的编译工具。

LLVM框架的主要组成部分有：

前端：负责将源代码转换为一种中间表示

优化器：负责优化中间代码

后端：生成可执行机器码的模块

LLVM为不同的语言提供了同一种中间表示LLVM IR，这样子如果我们需要开发一种新的语言的时候，我们只需要实现对应的前端模块，如果我们想要支持一种新的硬件，我们只需要实现对应的后端模块，其他部分可以复用。

## XLA目录结构

XLA的实现目录是tensorflow/compiler,目录结构如下：

## XLA编译
XLA也是基于LLVM框架开发的，前端的输入是Graph，前端没有将Graph直接转化为LLVM IR，而是转化为了XLA的自定义的中间表示HLO IR.并且为HLO IR设计了一系列的优化器。经过优化的HLO IR接下来会被转化为LLVM IR。

具体来说包含了下列几步：

+ 步骤一：由GraphDef创建Graph

+ 步骤二：由tensorflow.Graph编译为HLO IR

+ 步骤三：分析与优化HLO IR

+ 步骤四：由HLO IR转化为llvm IR

+ 步骤五：分析与优化llvm IR

+ 步骤六：生成特定平台的二进制文件

## AOT转化过程
+ Graph 到 HLO IR 的转化阶段
+ tensorflow.XlaCompiler.CompilerGraph函数将Graph编译成XLA的中间表示xla.UserComputation.
+ xla.ComputationTracker.BuildHloModule函数会将所有的xla.UserComputation转化为xla.HloComputation，并为之创建xla.HloModule.
    ***
+ Hlo IR 到 llvm IR的转化阶段
+ xla.CpuCompiler的输入是xla.HloModule，首先会调用RunHloPasses创建HloPassPipeline，添加并运行一系列的HloPass.
+ 优化：xla.AlebraicSimplifier(代数简化),xla.HloConstantFolding（常量折叠）,xla.HloCSE（公共表达式消除）等。
+ HloPassPipeline优化HLO IR之后，将创建xla.cpu.IrEmitter，将xla.HloModule中的每个xla.HloComputation转化为llvm IR表示，并创建对应的llvm.Module.
  ***
+ llvm ir到可执行机器码的转化
+ 创建xla.cpu.CompilerFunctor将llvm IR转化为最终的可执行机器代码llvm.object.ObjectFile.中间会调用一系列的llvm ir pass对llvm ir进行优化处理。

## AOT例子

+ 由GraphDef构建tensorflow.Graph。
+ 调用xla.XlaCompiler.CompileGraph，将tensorflow.Graph编译为xla.Computation。
+ 调用xla.CompileOnlyClient.CompileAheadOfTime函数，将xla.Computation编译为可执行代码。
+ 保存编译结果到头文件和object文件
+ 编写配置：test_graph_tfmatmul.config.pbtxt
+ 使用tf_library构建宏来编译子图为静态链接库
+ 编写代码以调用子图：test_graph_tfmatmul.h 
+ 引用头文件，编写使用端代码
+ 使用cc_binary创建最终的可执行二进制文件：BUILD
