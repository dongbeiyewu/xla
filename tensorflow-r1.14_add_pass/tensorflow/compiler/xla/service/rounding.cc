#include "tensorflow/compiler/xla/service/Multi_simplification.h"

#include <list>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "absl/container/flat_hash_set.h"
#include "absl/container/inlined_vector.h"
#include "tensorflow/compiler/xla/layout_util.h"
#include "tensorflow/compiler/xla/literal.h"
#include "tensorflow/compiler/xla/service/hlo_computation.h"
#include "tensorflow/compiler/xla/service/hlo_domain_map.h"
#include "tensorflow/compiler/xla/service/hlo_instruction.h"
#include "tensorflow/compiler/xla/service/hlo_opcode.h"
#include "tensorflow/compiler/xla/service/rounding.h"
#include "tensorflow/compiler/xla/shape_util.h"
#include "tensorflow/compiler/xla/types.h"
#include "tensorflow/core/lib/core/errors.h"
#include "tensorflow/core/lib/hash/hash.h"

namespace xla {


namespace {
// 找到乘法计算，进行四舍五入   

StatusOr<bool> rounding::Run(HloModule* module){
  bool changed = false;

  VLOG(2) << "Before rouding_simplification:";
  XLA_VLOG_LINES(2, module->ToString());

  for(auto* computation : module->MakeComputationPostOrder()){

    for(auto* instruction : computation->instructions()){
      if (instruction != computation->root_instruction() &&
          instruction == add{
          
            changed = true;
      }
    }

  }
    
}

VLOG(2) << "After rouding_simplification:";
return changed;

}
}