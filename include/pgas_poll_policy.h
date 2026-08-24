#ifndef PGAS_POLL_POLICY_H
#define PGAS_POLL_POLICY_H

#include "cxl_backend.h"

#include <chrono>
#include <cstdint>
#include <string>

enum class PgasPollAction {
    Spin,
    Yield,
    Sleep,
};

struct PgasPollConfig {
    static constexpr uint32_t kDefaultWorkerCount = 4;
    static constexpr uint32_t kDefaultSpinUsec = 50;
    static constexpr uint32_t kDefaultYieldCount = 10;
    static constexpr uint32_t kDefaultIdleSleepUsec = 100;
    static constexpr uint32_t kMaxWorkers = CXL_SHM_MAX_SLOTS;
    static constexpr uint32_t kMaxSpinUsec = 1'000'000;

    uint32_t worker_count = kDefaultWorkerCount;
    uint32_t spin_usec = kDefaultSpinUsec;
    uint32_t yield_count = kDefaultYieldCount;
    uint32_t idle_sleep_usec = kDefaultIdleSleepUsec;
    bool record_accesses = false;

    bool validate(std::string* error = nullptr) const {
        if (worker_count == 0 || worker_count > kMaxWorkers) {
            if (error) {
                *error = "pgas-workers must be between 1 and " + std::to_string(kMaxWorkers);
            }
            return false;
        }
        if (spin_usec > kMaxSpinUsec) {
            if (error) {
                *error = "pgas-spin-us must be between 0 and " +
                    std::to_string(kMaxSpinUsec);
            }
            return false;
        }
        if (idle_sleep_usec == 0) {
            if (error) {
                *error = "pgas-idle-sleep-us must be greater than 0";
            }
            return false;
        }
        return true;
    }
};

class PgasPollPolicy {
public:
    using Clock = std::chrono::steady_clock;
    using TimePoint = Clock::time_point;
    using NowFn = TimePoint (*)();

    explicit PgasPollPolicy(const PgasPollConfig& config, NowFn now_fn = defaultNow)
        : config_(config), now_fn_(now_fn) {}

    PgasPollAction onIdle() {
        const TimePoint now = now_fn_();
        const uint64_t next_idle_poll = idle_polls_ + 1;
        idle_polls_ = next_idle_poll;

        if (!idle_started_) {
            idle_started_ = true;
            idle_start_time_ = now;
            yielded_polls_ = 0;
        }

        if ((now - idle_start_time_) < std::chrono::microseconds(config_.spin_usec)) {
            return PgasPollAction::Spin;
        }

        if (yielded_polls_ < config_.yield_count) {
            yielded_polls_++;
            return PgasPollAction::Yield;
        }

        return PgasPollAction::Sleep;
    }

    void onActivity() {
        idle_started_ = false;
        yielded_polls_ = 0;
        idle_polls_ = 0;
    }

    uint64_t idle_polls() const {
        return idle_polls_;
    }

private:
    static TimePoint defaultNow() {
        return Clock::now();
    }

    PgasPollConfig config_;
    NowFn now_fn_;
    bool idle_started_ = false;
    TimePoint idle_start_time_{};
    uint32_t yielded_polls_ = 0;
    uint64_t idle_polls_ = 0;
};

#endif  // PGAS_POLL_POLICY_H
