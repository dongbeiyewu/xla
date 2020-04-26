# 使用JIT编译

## 为什么要使用即时（JIT）编译？

TensorFlow / XLA JIT编译器通过XLA编译和运行TensorFlow图形的一部分。与标准TensorFlow实现相比，这样做的好处是XLA可以将多个运算符（内核融合）融合到少量的编译内核中。与TensorFlow执行程序一样，与一次执行操作员相比，定位操作员可以减少内存带宽要求并提高性能。

## 通过XLA运行TensorFlow图表

有两种方法通过XLA运行TensorFlow计算，或者通过JIT编译操作员放置在CPU或GPU的设备上，或通过将操作员在`XLA_CPU`或`XLA_GPUTensorFlow`设备。将操作员直接放在TensorFlow XLA设备上强制操作员在该设备上运行，主要用于测试。

### 打开JIT编译

JIT编译可以在会话级别打开或手动进行选择操作。这两种方法都是零拷贝---在编译的XLA内核和置于同一设备上的TensorFlow操作符之间传递数据时，不需要复制数据。

### Session

在会话级别打开JIT编译会导致所有可能的操作符被贪婪地编译成XLA计算。每个XLA计算将被编译为一个或多个内核设备。

受限于一些限制，如果图中有两个相邻的运算符都具有XLA实现，那么它们将被编译为单个XLA计算。

JIT编译在会话级别打开，方法是在会话初始化期间将config 设置`global_jit_level`为`tf.OptimizerOptions.ON_1`并传递配置。

>`# Config to turn on JIT compilation`
</br>
`config = tf.ConfigProto()`</br>
`config.graph_options.optimizer_options.global_jit_level = tf.OptimizerOptions.ON_1`
</br>
`sess = tf.Session(config=config)`

### 手动

JIT编译也可以为一个或多个操作员手动打开。这是通过标记操作符以使用属性进行编译完成的`_XlaCompile=true`。最简单的方法是通过在中`tf.contrib.compiler.jit.experimental_jit_scope()`定义的范围`tensorflow/contrib/compiler/jit.py`。用法示例：

```python
jit_scope = tf.contrib.compiler.jit.experimental_jit_scope
x = tf.placeholder(np.float32)
with jit_scope():
  y = tf.add(x, x)  # The "add" will be compiled with XLA.
```

运行`tensorflow/examples/tutorials/mnist`下的例子`mnist_softmax_xla.py`

其中

>` mnist = input_data.read_data_sets(FLAGS.data_dir)`

下载mnist失败，将下载好的mnist数据集放在文件夹下，更改代码为

>` mnist = input_data.read_data_sets('./')`

加了把计算图结构写入tensorboard文件的代码。

>`writer = tf.summary.FileWriter( './train', sess.graph )
  writer.close()`

运行后计算结构图已经生成

![1](https://github.com/erguixieshen/XLA/raw/master/week2/picture/3.png)

### TensorBoard可视化

>`#运行TensorBoard，并将日志的地址指向上面程序日志输出的地址`</br>
`tensorboard --logdir=train`

运行以上命令会启动一个服务，这个服务的端口默认为6006（使用--port参数可以改变启动服务的端口）。运行结果如下图：

![1](https://github.com/erguixieshen/XLA/raw/master/week2/picture/4.png)

浏览器访问默认端口

>`http://DESKTOP-UHLM3HL:6006/`

可以看到mnist的计算图

![1](https://github.com/erguixieshen/XLA/raw/master/week2/picture/5.png)

