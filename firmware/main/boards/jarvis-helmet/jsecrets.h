// Read-only access to the board's secrets: NVS partition "jsecrets", namespace "secrets"
// (contract D13, D27), written by tools/provision_secrets.py. Keys: cf_id / cf_secret
// (cf_access.cc) and mcp_token (local_mcp_server.cc). Nothing here writes or erases the
// partition, and values are never logged.
#pragma once

#include <cstddef>
#include <string>

namespace jsecrets {

enum class Status {
    kFound,       // `out` holds the value
    kAbsent,      // no jsecrets partition in the table, or namespace/key not provisioned
    kUnreadable,  // partition or key present but unusable (NVS error, value too long)
};

// Reads string `key` of at most `max_length` characters. Mounts the partition on first
// use; call from a task with an internal-RAM stack (flash access).
Status ReadString(const char* key, size_t max_length, std::string& out);

}  // namespace jsecrets
