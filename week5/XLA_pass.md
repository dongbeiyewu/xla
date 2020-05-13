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
![]([pic/3.png](https://github.com/dongbeiyewu/xla/raw/master/week5/pic/2.png))
## aot方式如何选择pass