## XLA目录结构
XLA的实现目录是tensorflow/compiler,目录结构如下：
|目录名	|功能|
| ------------- | ------------- |
|aot	| aot编译相关代码，前面分析的tfcompile_tool代码就在这里|
|jit	| jit编译相关代码，例如xlalaunch节点的OpKenel、XLA相关的计算图重构，都在这里|
|plugin	| 此模块看起来还没完成，暂不分析|
|tests	| 测试代码 |
|tf2xla	| GraphDef转化为XLA Hlo IR代码|
|xla	| xla编译器核心代码，HLO IR转化为LLVM IR以及机器码的生成|

XLA Hlo IR代码生成
[xla.proto](https://github.com/tensorflow/tensorflow/blob/master/tensorflow/compiler/xla/xla.proto)

使用命令
>`XLA_FLAGS="--xla_dump_to=/some/path --xla_dump_hlo_pass_re=.* --xla_dump_hlo_as_html" python your_program.py`
+ xla_dump_to: 希望生成的中间表示存放在哪里
+ xla_dump_hlo_pass_re: 默认xla是不会导出hlo内部pass的，但是使用这个选项后可以导出对应的pass，.*表示所有pass
+ xla_dump_hlo_as_html: 用html格式导出hlo，还可以用xla_dump_hlo_as_{text, proto, ...}