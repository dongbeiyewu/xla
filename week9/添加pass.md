# 添加一个pass

本次任务的内容：实现一个xla HLO pass, 将两个浮点数的乘积自动四舍五入为整数
脚本程序`xla_pass_test.py`在正常的tensorflow环境下会报断言错误，如果pass实现正确，打开XLA后，该脚本应该能运行通过
## 编写文件
- [x] 在源码下添加文件
  ``` 
  tensorflow-r1.14_add_pass\tensorflow\compiler\xla\service\Multi_simplification.h
  tensorflow-r1.14_add_pass\tensorflow\compiler\xla\service\Multi_simplification.cc
  tensorflow-r1.14_add_pass\tensorflow\compiler\xla\service\Multi_simplification_test.cc
  ```

- [x] `Multi_simplification.h`中定义构造函数，析构函数
- [ ] `Multi_simplification.cc`中遍历`computation->instructions()`，找到乘法操作进行四舍五入
- [ ] `Multi_simplification_test.cc`中测试`hlo_model`经过这个pass是否有效
- [ ] 在`cpu_compliler.cc`中使用管道调用Multisimple

    `pipeline.AddPass<Multisimple>(/*is_folat_mul=*/true);`
- [ ] 编译tf，在tf环境下运行



