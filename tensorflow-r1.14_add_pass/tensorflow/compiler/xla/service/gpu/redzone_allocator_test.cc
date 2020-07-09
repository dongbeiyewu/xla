/* Copyright 2019 The TensorFlow Authors. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
==============================================================================*/

#include "tensorflow/compiler/xla/service/gpu/redzone_allocator.h"

#include "tensorflow/compiler/xla/service/hlo_module_config.h"
#include "tensorflow/compiler/xla/status_macros.h"
#include "tensorflow/compiler/xla/test.h"
#include "tensorflow/core/lib/core/status_test_util.h"
#include "tensorflow/core/platform/test.h"
#include "tensorflow/core/platform/test_benchmark.h"
#include "tensorflow/stream_executor/device_memory_allocator.h"
#include "tensorflow/stream_executor/multi_platform_manager.h"
#include "tensorflow/stream_executor/platform.h"

namespace xla {
namespace gpu {
namespace {

using RedzoneCheckStatus = RedzoneAllocator::RedzoneCheckStatus;

static void EXPECT_REDZONE_OK(StatusOr<RedzoneCheckStatus> status) {
  EXPECT_TRUE(status.ok());
  EXPECT_TRUE(status.ValueOrDie().ok());
}

static void EXPECT_REDZONE_VIOLATION(StatusOr<RedzoneCheckStatus> status) {
  EXPECT_TRUE(status.ok());
  EXPECT_FALSE(status.ValueOrDie().ok());
}

TEST(RedzoneAllocatorTest, WriteToRedzone) {
  constexpr int64 kRedzoneSize = 1 << 23;  // 8MiB redzone on each side
  // Redzone pattern should not be equal to zero; otherwise modify_redzone will
  // break.
  constexpr uint8 kRedzonePattern = 0x7e;

  // Allocate 32MiB + 1 byte (to make things misaligned)
  constexpr int64 kAllocSize = (1 << 25) + 1;

  se::Platform* platform =
      se::MultiPlatformManager::PlatformWithName("cuda").ValueOrDie();
  se::StreamExecutor* stream_exec = platform->ExecutorForDevice(0).ValueOrDie();
  HloModuleConfig config;
  se::StreamExecutorMemoryAllocator se_allocator(platform, {stream_exec});
  RedzoneAllocator allocator(/*device_ordinal=*/0, &se_allocator, config,
                             kRedzoneSize, kRedzonePattern);

  se::Stream stream(stream_exec);
  stream.Init();
  TF_ASSERT_OK_AND_ASSIGN(se::DeviceMemory<uint8> buf,
                          allocator.AllocateBytes(&stream,
                                                  /*byte_size=*/kAllocSize));
  EXPECT_REDZONE_OK(allocator.CheckRedzones(&stream));

  char* buf_addr = reinterpret_cast<char*>(buf.opaque());
  se::DeviceMemoryBase lhs_redzone(buf_addr - kRedzoneSize, kRedzoneSize);
  se::DeviceMemoryBase rhs_redzone(buf_addr + kAllocSize, kRedzoneSize);

  // Check that the redzones are in fact filled with kRedzonePattern.
  auto check_redzone = [&](se::DeviceMemoryBase redzone,
                           absl::string_view name) {
    std::vector<uint8> host_buf(kRedzoneSize);
    TF_ASSERT_OK(stream.ThenMemcpy(host_buf.data(), redzone, kRedzoneSize)
                     .BlockHostUntilDone());
    const int64 kMaxMismatches = 16;
    int64 mismatches = 0;
    for (int64 i = 0; i < host_buf.size(); ++i) {
      if (mismatches == kMaxMismatches) {
        ADD_FAILURE() << "Hit max number of mismatches; skipping others.";
        break;
      }
      if (host_buf[i] != kRedzonePattern) {
        ++mismatches;
        EXPECT_EQ(host_buf[i], kRedzonePattern)
            << "at index " << i << " of " << name << " redzone";
      }
    }
  };
  check_redzone(lhs_redzone, "lhs");
  check_redzone(rhs_redzone, "rhs");

  // Modifies a redzone, checks that RedzonesAreUnmodified returns false, then
  // reverts it back to its original value and checks that RedzonesAreUnmodified
  // returns true.
  auto modify_redzone = [&](se::DeviceMemoryBase redzone, int64 offset,
                            absl::string_view name) {
    SCOPED_TRACE(absl::StrCat(name, ", offset=", offset));
    se::DeviceMemoryBase redzone_at_offset(
        reinterpret_cast<char*>(redzone.opaque()) + offset, 1);
    char old_redzone_value = 0;
    {
      XLA_SCOPED_LOGGING_TIMER("Checking redzones");
      EXPECT_REDZONE_OK(allocator.CheckRedzones(&stream));
    }
    stream.ThenMemcpy(&old_redzone_value, redzone_at_offset, 1)
        .ThenMemZero(&redzone_at_offset, 1);
    EXPECT_REDZONE_VIOLATION(allocator.CheckRedzones(&stream));
    stream.ThenMemcpy(&redzone_at_offset, &old_redzone_value, 1);
    EXPECT_REDZONE_OK(allocator.CheckRedzones(&stream));
  };

  modify_redzone(lhs_redzone, /*offset=*/0, "lhs");
  modify_redzone(lhs_redzone, /*offset=*/kRedzoneSize - 1, "lhs");
  modify_redzone(rhs_redzone, /*offset=*/0, "rhs");
  modify_redzone(rhs_redzone, /*offset=*/kRedzoneSize - 1, "rhs");
}

// Older CUDA compute capabilities (<= 2.0) have a limitation that grid
// dimension X cannot be larger than 65535.
//
// Make sure we can launch kernels on sizes larger than that, given that the
// maximum number of threads per block is 1024.
TEST(RedzoneAllocatorTest, VeryLargeRedzone) {
  // Make sure the redzone size would require grid dimension > 65535.
  constexpr int64 kRedzoneSize = 65535 * 1024 + 1;
  se::Platform* platform =
      se::MultiPlatformManager::PlatformWithName("cuda").ValueOrDie();
  se::StreamExecutor* stream_exec = platform->ExecutorForDevice(0).ValueOrDie();
  HloModuleConfig config;
  se::StreamExecutorMemoryAllocator se_allocator(platform, {stream_exec});
  RedzoneAllocator allocator(/*device_ordinal=*/0, &se_allocator, config,
                             kRedzoneSize, /*redzone_pattern=*/-1);
  se::Stream stream(stream_exec);
  stream.Init();
  (void)allocator.AllocateBytes(&stream, /*byte_size=*/1);
  EXPECT_REDZONE_OK(allocator.CheckRedzones(&stream));
}

}  // namespace
}  // namespace gpu
}  // namespace xla
