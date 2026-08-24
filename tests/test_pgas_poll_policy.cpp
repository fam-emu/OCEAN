#include "pgas_poll_policy.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>

namespace {

using FakeClock = PgasPollPolicy::Clock;

FakeClock::time_point g_now;

FakeClock::time_point fakeNow() {
    return g_now;
}

void setNowUsec(uint64_t usec) {
    g_now = FakeClock::time_point(std::chrono::microseconds(usec));
}

void expect(bool condition, const std::string& message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

void testSpinYieldSleepTransitions() {
    PgasPollConfig config;
    config.spin_usec = 50;
    config.yield_count = 2;
    config.idle_sleep_usec = 100;

    setNowUsec(0);
    PgasPollPolicy policy(config, fakeNow);

    expect(policy.onIdle() == PgasPollAction::Spin, "idle poll at 0us should spin");
    setNowUsec(49);
    expect(policy.onIdle() == PgasPollAction::Spin, "idle poll before spin boundary should spin");
    setNowUsec(50);
    expect(policy.onIdle() == PgasPollAction::Yield, "idle poll at spin boundary should yield");
    setNowUsec(51);
    expect(policy.onIdle() == PgasPollAction::Yield, "yield phase should continue for configured count");
    setNowUsec(52);
    expect(policy.onIdle() == PgasPollAction::Sleep, "idle poll after yield budget should sleep");
}

void testActivityResetsBackToSpin() {
    PgasPollConfig config;
    config.spin_usec = 1;
    config.yield_count = 1;

    setNowUsec(0);
    PgasPollPolicy policy(config, fakeNow);

    expect(policy.onIdle() == PgasPollAction::Spin, "first idle poll should spin");
    setNowUsec(1);
    expect(policy.onIdle() == PgasPollAction::Yield, "second idle poll should yield");
    policy.onActivity();
    setNowUsec(1);
    expect(policy.onIdle() == PgasPollAction::Spin, "activity should reset policy to spin");
}

void testWorkerBoundsValidation() {
    PgasPollConfig minimum_workers;
    minimum_workers.worker_count = 1;
    expect(minimum_workers.validate(), "worker_count=1 should be valid");

    PgasPollConfig maximum_workers;
    maximum_workers.worker_count = PgasPollConfig::kMaxWorkers;
    expect(maximum_workers.validate(), "worker_count=max should be valid");

    PgasPollConfig zero_workers;
    zero_workers.worker_count = 0;
    expect(!zero_workers.validate(), "worker_count=0 should be invalid");

    PgasPollConfig too_many_workers;
    too_many_workers.worker_count = PgasPollConfig::kMaxWorkers + 1;
    expect(!too_many_workers.validate(), "worker_count above max should be invalid");
}

void testSpinValidation() {
    PgasPollConfig valid_spin;
    valid_spin.spin_usec = PgasPollConfig::kMaxSpinUsec;
    expect(valid_spin.validate(), "spin_usec=max should be valid");

    PgasPollConfig too_much_spin;
    too_much_spin.spin_usec = PgasPollConfig::kMaxSpinUsec + 1;
    expect(!too_much_spin.validate(), "spin_usec above max should be invalid");
}

void testDefaultRecordAccessesIsDisabled() {
    PgasPollConfig config;
    expect(!config.record_accesses, "record_accesses should default to false");
}

}  // namespace

int main() {
    testSpinYieldSleepTransitions();
    testActivityResetsBackToSpin();
    testWorkerBoundsValidation();
    testSpinValidation();
    testDefaultRecordAccessesIsDisabled();
    return 0;
}
