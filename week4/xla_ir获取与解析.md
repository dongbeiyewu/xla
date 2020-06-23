# XLA优化过程

## 实际运行

用以下命令
>`XLA_FLAGS="--xla_dump_to=/some/path --xla_dump_hlo_pass_re=.* --xla_dump_hlo_as_html" python your_program.py`

+ xla_dump_to: 希望生成的中间表示存放在哪里
+ xla_dump_hlo_pass_re: 默认xla是不会导出hlo内部pass的，但是使用这个选项后可以导出对应的pass，.*表示所有pass
+ xla_dump_hlo_as_html: 用html格式导出hlo，还可以用xla_dump_hlo_as_{text, proto, ...}

完整的选项信息在[xla.proto](https://github.com/tensorflow/tensorflow/blob/master/tensorflow/compiler/xla/xla.proto)里

## 内部功能解析
XLA 的全称是 Accelerate Linear Algebra, HLO 的全称是 High Level Optimizer。 HLO 具有自己的文法，在`tensorflow/compiler/xla/service/g3doc/hlo_parser.md`中记录了 HLO 的完整文法，其内容如下：

``` h
hlo_module
  : 'HloModule' name computations
  ;

/* If no computation is marked as ENTRY, the last computation will be the entry
computation of the module.*/
computations
  : computation
  | computation computations
  ;

computation
  : 'ENTRY' name param_list_to_shape instruction_list
  | name param_list_to_shape instruction_list
  | 'ENTRY' name instruction_list
  | name instruction_list
  ;

/* If no instruction is marked as ROOT, the last instruction will be the root of
its computation. */
instruction_list
  : '{' instruction_list1 '}'
  ;
instruction_list1
  : instruction
  | instruction_list1 instruction
  ;
instruction
  : 'ROOT' name '=' shape opcode operands extra_attributes
  | name '=' shape opcode operands extra_attributes
  ;

operands
  : '(' operands1 ')'
  ;
operands1
  : /*empty*/
  | operand
  | operands1 ',' operand
  ;
operand
  : shape name
  | name
  ;

attributes
  : /*empty*/
  | ',' attribute
  | ',' attribute attributes
  ;
attribute
  : attribute_name attribute_value
  ;
attribute_value
  : kInt
  | kName
  | [0-9bf]{2,}_[0-9io]{2,}->[0-9bf]{2,}                /*dim_labels_pattern*/
  | [0-9]+(x[0-9]+)+                                    /*dxd_pattern*/
  | [0-9]+_[0-9]+(_[0-9]+)?(x[0-9]+_[0-9]+(_[0-9]+)?)*  /*pad_pattern*/
  | '{' sub_attributes '}'
  ;

param_list_to_shape
  : param_list '->' shape
  ;

param_list
  : '(' param_list1 ')'
  ;
param_list1
  : /*empty*/
  | param
  | param_list1 ',' param
  ;
param
  : name shape
  ;

shape
  : shape_val_
  | '(' tuple_elements ')'
  ;
tuple_elements
  : /*empty*/
  | shape (',' shape)*
  ;

name
  : identifier ':'
  | '%' identifier
  | identifier
  ;

identifier
  : [a-zA-Z_][a-zA-Z0-9_.-]*
  ;

/* literal is in the right hand side of a constant instruction. */
literal
  : tuple
  | non_tuple
  ;
tuple
  : shape '(' literal_list ')'
  ;
literal_list
  : /*empty*/
  : literal
  | literal_list ',' literal
  ;
non_tuple
  : rank01
  | rank2345
  ;
rank2345
  : nested_array
  ;
```
通过 xla_dump_hlo_as_text 可以得到 HLO 的文本形式记录

从全局来看，HLO 经过的 pass 用伪代码可以写为：

``` python
def simplification():
    batchnorm_expander()
    algsimp()
    simplify-sorts()
    tuple-simplifier()
    while-loop-constant-sinking()
    simplify-while-loops()
    slice-sinker()
    dce()
    reshape-mover()
    constant-folding()
    simplify-conditional()

def optimization():
    zero_sized_hlo_eliminatioin()
    dynamic-index-splitter()
    gpu_hlo_support_checker()
    CallInliner()
    dot_decomposer()
    convolution-group-converter()
    stable-sort-expander()
    element_type_converter()
    for i in range(3):
        simplification()
    hlo-get-dimension-size-rewriter()
    zero_sized_hlo_elimination()
    transpose-folding()
    cse()
    dce()
    while-loop-trip-count-annotator()

def conv_canonicalization():
    consolver-rewriter()
    cudnn-conv-rewriter()
    cudnn-fused-convolution()
    cudnn-conv-padding()
    constant-folding()

def layout_assignment():
    _layout-assignment()

def post-layout_assignment():
    algsimp()
    cudnn-conv-algorithm-picker()
    tuple-simplifier()
    cse()

def fusion():
    variadic-op-splitter
    for i in range(2):
        _fusion()
    fusion_merger()
    multi_output_fusion()
    cse()
    dce()

def copy-insertion():
    adding_copies_to_resolve_interference()
    removing_unnecessary_copies()
    adding_special-case_copies()

def GPU-ir-emit-prepare():
    dce()
    flatten-call-graph()
    copy-insertion()
    sanitize-constant-names()

def main():
    optimization()
    conv_canonicalization()
    layout_assignment()
    post-layout_assignment()
    for i in range(3):
        fusion()
    reduce-precision()
    GPU-ir-emit-prepare()
```

