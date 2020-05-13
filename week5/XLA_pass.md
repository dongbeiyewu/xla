## jit方式如何选择pass
JIT全称Just In Time（即时）.在即时编译中，计算图在不会在运行阶段前被编译成可执行代码，而是在进入运行阶段后的适当的时机才会被编译成可执行代码，并且可以被直接调用了。
### 方式一、通过Session设置：
Python API中打开JIT支持的方式有以下几种：
这种方式的影响是Session范围的，内核会编译尽可能多的节点。
``` python
# Config to turn on JIT compilation
config = tf.ConfigProto()
config.graph_options.optimizer_options.global_jit_level = tf.OptimizerOptions.ON_1

sess = tf.Session(config=config)
```
### 方式二、通过tf.contrib.compiler.jit.experimental_jit_scope():
这种方式影响scope内的所有节点，这种方式会对Scope内的所有节点添加一个属性并设置为true: _XlaCompile=true.
``` python
jit_scope = tf.contrib.compiler.jit.experimental_jit_scope

x = tf.placeholder(np.float32)
with jit_scope():
   y = tf.add(x, x)  # The "add" will be compiled with XLA.
```
### 方式三、通过设置device:
``` python
with tf.device("/job:localhost/replica:0/task:0/device:XLA_GPU:0"):
  output = tf.add(input1, input2)
```
### 常规计算图构建
![](https://github.com/dongbeiyewu/xla/raw/master/week5/pic/3.png)

第一步、 TF_NewGraph会创建一个tensorflow.Graph对象，这就是计算图在TF内核中的表示；TF_NewGraph返回的结果是TF_Graph的指针，这个结构体是C API层对tensorflow.Graph的封装对象。

第二步、 TF_NewOperation创建Graph中的Node，这一步中涉及的类比较多，tensorflow.NodeBuilder,tensorflow.NodeDefBuilder是为了构建tensorflow.NodeDef的工具类；为了最终构建Node对象，还需要通过tensorflow.OpRegistryInterface来找到Node绑定的OpDef。就像前面说的，Op是通过注册来提供给tf使用的。

细心的用户发现，其实这步并没有创建Node对象，为什么呢？我们先往后看。

第三步、设置Node的输入，设备以及属性，如图1中调用10到22。

**最后，**TF_FinishOperation创建Node对象，并添加到Graph中。我们看到，实际的Node对象的创建是到这一步才发生的（调用26），并且根据节点的输入和控制输入，添加所需的数据边和流控制边。这也是为什么Node对象的创建放在最后一步的原因。


### 接口层的设置对内核中计算图的影响
graph在运行前，需要经过一系列优化和重构。其中一步涉及到类：tensorflow.OptimizationPassRegistry，此类可以运行其中注册的tensorflow.GraphOptimizationPass的子类，每一个子类都是实现了一种graph的优化和重构的逻辑。XLA JIT 相关的Graph优化和重构，也是通过这个入口来执行的。

JIT相关的[tensorflow.GraphOptimizationPass](https://github.com/tensorflow/tensorflow/blob/master/tensorflow/compiler/jit/jit_compilation_pass_registration.cc)

可以看到JIT编译相关的tensorflow.GraphOptimizationPass有三个：

### tensorflow.MarkForCompilationPass：
上面提到的开启JIT的三种设置方式，就是在此类中进行检查的。通过检查这些设置，此类首先会挑选出所有开启JIT并且目前版本支持JIT编译的节点，并且运行聚类分析，将这些等待JIT编译的节点分到若干个Cluster中，看一下下面的例子：
### tensorflow.EncapsulateSubgraphsPass：

这一步优化分三步，

第一步 ：为上一个优化类MarkForCompilationPass mark形成的cluster分别创建对应的SubGraph对象。

第二步：为每个SubGraph对象创建对应的FunctionDef，并将创建的FunctionDef添加到FunctionLibrary中。
Function可以看做一个独立的计算图，node_def就是这个子图包含的所有节点。Function可以被实例化和调用，方式是向调用方的计算图中插入一个Call节点，这类节点的运算核(OpKernel)是CallOp:

第三步：重新创建一张新的计算图，首先将原计算图中没有被mark的节点直接拷贝过来，然后为每个SubGraph对应的Function创建CallOp节点，最后创建计算图中数据和控制依赖关系。
### 3、tensorflow.BuildXlaLaunchOpsPass：
经过EncapsulateSubgraphsPass优化的计算图中的function call节点全部替换成xlalaunch节点。

JIT的关键就是这个xlalaunch节点。xlalaunch节点节点的运算名为”_XlaLaunch”,运算核是XlaLocalLaunchOp，按照运算核的要求它的父类也是OpKernel。

XlaLocalLaunchOp对外响应Executor的调用请求，对内调用JIT相关API类编译和执行FunctionDef。当然对编译结果会有缓存操作，没必要每次调用都走一次编译过程：

所以JIT编译流程为

![](https://github.com/dongbeiyewu/xla/raw/master/week5/pic/4.png)

+ JIT调用方式的入口在运算核tensorflow.XlaLocalLaunchOp.Compute，tensorflow.XlaLocalLaunchOp是连接外部Graph的Executor和内部JIT调用的桥梁。

+ 如果被调用的计算图缓存不命中，则会调用xla.XlaCompile进行实际的编译。

+ 编译过程类似AOT，不同之处主要在于：首先这次调用的Client和Service的实现类是xla.LocalClient和xla.LocalService；其次，llvm ir到机器码的编译过程，这次是通过xla.cpu.SimpleOrcJIT完成的，它将llvm ir编译为可执行代码，并可被立即调用。

+ 可执行机器码后续会被封装为xla.LocalExecutale

+ 调用xla.LocalExecutable的如后函数Run.
