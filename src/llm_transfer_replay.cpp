#include "llm_transfer_model.h"

#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

struct Options {
    std::string trace;
    std::string hardware_profile;
    std::string output;
    std::string metadata_output;
    std::string mode = "auto";
    std::string backend = "analytical";
};

void usage(std::ostream &stream) {
    stream << "Usage: llm_transfer_replay --trace transfer-events.csv "
              "--hardware-profile ocean-hardware-profile.yaml "
              "--output ocean-service-events.csv [--metadata-output "
              "replay-metadata.json] "
              "[--mode auto|aggregate|detailed] "
              "[--backend analytical|cxlmemsim-core]\n";
}

Options parseOptions(int argc, char **argv) {
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--help" || argument == "-h") {
            usage(std::cout);
            std::exit(0);
        }
        if (index + 1 >= argc)
            throw std::runtime_error("missing value after " + argument);
        const std::string value = argv[++index];
        if (argument == "--trace")
            options.trace = value;
        else if (argument == "--hardware-profile")
            options.hardware_profile = value;
        else if (argument == "--output")
            options.output = value;
        else if (argument == "--metadata-output")
            options.metadata_output = value;
        else if (argument == "--mode")
            options.mode = value;
        else if (argument == "--backend")
            options.backend = value;
        else
            throw std::runtime_error("unknown option: " + argument);
    }
    if (options.trace.empty() || options.hardware_profile.empty() || options.output.empty()) {
        throw std::runtime_error("--trace, --hardware-profile, and --output are required");
    }
    if (std::filesystem::path(options.trace).extension() != ".csv") {
        throw std::runtime_error("standalone OCEAN frontend accepts versioned CSV; "
                                 "use cxl-llm to convert Parquet");
    }
    if (std::filesystem::path(options.output).extension() != ".csv") {
        throw std::runtime_error("standalone OCEAN frontend writes CSV; use "
                                 "cxl-llm to convert it to Parquet");
    }
    if (options.metadata_output.empty())
        options.metadata_output = options.output + ".metadata.json";
    return options;
}

} // namespace

int main(int argc, char **argv) {
    try {
        const auto options = parseOptions(argc, argv);
        const auto profile = cxlmemsim::llm::loadHardwareProfile(options.hardware_profile);
        const auto requests = cxlmemsim::llm::loadTransferRequestsCsv(options.trace);
        const auto mode = cxlmemsim::llm::parseReplayMode(options.mode);
        const auto backend = cxlmemsim::llm::parseReplayBackend(options.backend);
        const cxlmemsim::llm::TensorTransferModel model(profile);
        const auto events = model.replay(requests, mode, backend);
        cxlmemsim::llm::writeServiceEventsCsv(options.output, events);
        cxlmemsim::llm::writeReplayMetadataJson(options.metadata_output, profile, events, mode, backend,
                                                model.evidence(), options.hardware_profile, options.trace);
        std::cout << "modeled " << events.size() << " LLM transfer events\n"
                  << "backend=" << cxlmemsim::llm::replayBackendName(backend) << '\n'
                  << "service_events=" << options.output << '\n'
                  << "metadata=" << options.metadata_output << '\n';
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "ERROR: " << error.what() << '\n';
        usage(std::cerr);
        return 2;
    }
}
