// Runs every shared sequence fixture (tools/tests/fixtures/sequences/*.json, contract 4.11)
// through the firmware validator. The server's Python tests load the same files, so both
// sides must agree on validity, the failing step number, the error text and the estimate.
//
// Fixture: {"name": str, "steps": <array or any JSON>, "valid": bool,
//           "error_step": int|null, "error_contains": str|null, "estimated_ms": int|null,
//           "note": str}
// error_step counts from 1 (contract D23); null on an invalid fixture means a top-level
// error (not an array, too many steps, too long).
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <cJSON.h>

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <system_error>
#include <vector>

#include "sequence_validator.h"

using Catch::Matchers::ContainsSubstring;
using Catch::Matchers::StartsWith;

namespace {

namespace fs = std::filesystem;
using CJsonPtr = std::unique_ptr<cJSON, decltype(&cJSON_Delete)>;

fs::path FixturesDir() {
    const char* env = std::getenv("JARVIS_SEQUENCE_FIXTURES_DIR");
    return env != nullptr && *env != '\0' ? fs::path(env) : fs::path(JARVIS_SEQUENCE_FIXTURES_DIR);
}

std::vector<fs::path> FixtureFiles(const fs::path& dir) {
    std::vector<fs::path> files;
    std::error_code ec;
    if (!fs::is_directory(dir, ec)) {
        return files;
    }
    for (const auto& entry : fs::directory_iterator(dir, ec)) {
        if (entry.is_regular_file(ec) && entry.path().extension() == ".json") {
            files.push_back(entry.path());
        }
    }
    std::sort(files.begin(), files.end());
    return files;
}

std::string ReadFile(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    std::stringstream buffer;
    buffer << in.rdbuf();
    return buffer.str();
}

std::string StringField(const cJSON* object, const char* key) {
    const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
    return cJSON_IsString(item) && item->valuestring != nullptr ? item->valuestring : "";
}

bool StartsWithText(const std::string& s, const std::string& prefix) {
    return s.compare(0, prefix.size(), prefix) == 0;
}

}  // namespace

TEST_CASE("shared sequence fixtures agree with the firmware validator", "[fixtures]") {
    const fs::path dir = FixturesDir();
    const std::vector<fs::path> files = FixtureFiles(dir);
    if (files.empty()) {
        SKIP("no sequence fixtures found in " << dir.string());
    }

    for (const fs::path& file : files) {
        const std::string content = ReadFile(file);
        CJsonPtr fixture(cJSON_Parse(content.c_str()), &cJSON_Delete);
        INFO("fixture " << file.filename().string());
        if (!cJSON_IsObject(fixture.get())) {
            FAIL_CHECK("fixture is not a JSON object");
            continue;
        }
        const cJSON* steps = cJSON_GetObjectItemCaseSensitive(fixture.get(), "steps");
        const cJSON* valid = cJSON_GetObjectItemCaseSensitive(fixture.get(), "valid");
        if (steps == nullptr || !cJSON_IsBool(valid)) {
            FAIL_CHECK("fixture needs \"steps\" and a boolean \"valid\"");
            continue;
        }
        const cJSON* error_step = cJSON_GetObjectItemCaseSensitive(fixture.get(), "error_step");
        const cJSON* error_contains =
            cJSON_GetObjectItemCaseSensitive(fixture.get(), "error_contains");
        const cJSON* estimated = cJSON_GetObjectItemCaseSensitive(fixture.get(), "estimated_ms");

        std::unique_ptr<char, decltype(&cJSON_free)> printed(cJSON_PrintUnformatted(steps),
                                                             &cJSON_free);
        REQUIRE(printed != nullptr);
        const std::string steps_json = printed.get();
        const seq::ValidationResult result = seq::Validate(steps_json);

        INFO("name: " << StringField(fixture.get(), "name"));
        INFO("note: " << StringField(fixture.get(), "note"));
        INFO("steps: " << steps_json.substr(0, 300));
        INFO("validator error: " << result.error);

        const bool expect_valid = cJSON_IsTrue(valid) != 0;
        CHECK(result.ok == expect_valid);
        if (!expect_valid && !result.ok) {
            if (cJSON_IsNumber(error_step)) {
                CHECK_THAT(
                    result.error,
                    StartsWith("invalid step #" +
                               std::to_string(static_cast<int>(error_step->valuedouble)) + ": "));
            } else {
                CHECK_THAT(result.error, !StartsWith("invalid step #"));
            }
            if (cJSON_IsString(error_contains) && error_contains->valuestring != nullptr) {
                CHECK_THAT(result.error, ContainsSubstring(error_contains->valuestring));
            }
        }
        if (cJSON_IsNumber(estimated)) {
            // The validator only has an estimate once every step parsed.
            if (result.ok || StartsWithText(result.error, "sequence too long")) {
                CHECK(result.sequence.estimated_ms == static_cast<int>(estimated->valuedouble));
            } else {
                WARN("estimated_ms given but the sequence did not parse: not compared");
            }
        }
    }
}
