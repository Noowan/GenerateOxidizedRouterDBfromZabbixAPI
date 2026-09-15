# Zabbix to Oxidized Exporter

A safe command-line exporter that retrieves active network devices from the Zabbix API and generates an Oxidized `router.db` file.

The exporter uses Zabbix host tags to decide which devices should be managed by Oxidized, which Oxidized model to use, and optionally which management address to write.

Default output format:

```text
hostname:address:model
```

Example:

```text
core-router-01:10.10.10.1:ios
edge-router-01:edge-router-01.example.net:junos
```

## Features

- Retrieves enabled hosts through the Zabbix JSON-RPC API.
- Supports Bearer and JSON-RPC token authentication.
- Selects devices explicitly through configurable Zabbix tags.
- Reads the Oxidized model and optional management address from tags.
- Selects an appropriate Zabbix interface when no address override exists.
- Prefers SNMP interfaces over Zabbix Agent and other interface types.
- Validates names, addresses, models, delimiters, and duplicate names.
- Sorts devices by technical host name.
- Prevents concurrent runs with an exclusive file lock.
- Writes `router.db` atomically through a temporary file.
- Preserves existing ownership and permissions where possible.
- Creates a backup of the previous file by default.
- Skips rewriting the file when its content has not changed.
- Protects against empty or unexpectedly reduced device lists.
- Provides dry-run and invalid-host skipping modes.
- Supports the system CA store, a custom CA file, or insecure TLS mode.

## Requirements

- Linux or another Unix-like operating system with `fcntl.flock` support
- Python 3
- Access to the Zabbix JSON-RPC API
- A Zabbix API token with permission to read hosts, interfaces, and tags
- Dependencies from `requirements.txt`

> The exporter uses Unix-specific file-locking and directory-synchronization functions. It does not run on Windows without modifications.

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/Noowan/GenerateOxidizedRouterDBfromZabbixAPI.git
cd GenerateOxidizedRouterDBfromZabbixAPI/
```
### 2. Create and activate a virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

A minimal `requirements.txt` contains:

```text
requests
```

### 4. Create and edit the environment file

```bash
cp params_example.env params.env
nano params.env
```

Example `.env`:

```dotenv
ZABBIX_URL="https://zabbix.example.local"
ZABBIX_TOKEN="insert-api-token"
ZABBIX_AUTH_MODE="bearer"

ROUTER_DB="/srv/oxidized/config/router.db" #your path to router.db file

OXIDIZED_ENABLED_TAG="oxidized_enabled" #name of tag in zabbix to gather devices
OXIDIZED_MODEL_TAG="oxidized_model" #name of tag in zabbix to match device to oxidized models
OXIDIZED_ADDRESS_TAG="oxidized_address" #name of tag in zabbix to override IP address of the device in oxidized

OXIDIZED_MIN_HOSTS="1" #estimated number of devices in the file. If router.db contains less devices there will be an exception
OXIDIZED_MAX_DROP_PERCENT="15" #how much devices script will drop from router.db if they are deleted in zabbix
```

Protect the file because it contains an API token:

```bash
chmod 600 params.env
```

### 5. Import variables from `params.env`

The script reads environment variables but does not parse `.env` automatically. Export the values into the current shell:

```bash
set -a
source params.env
set +a
```

Repeat this step in every new shell session unless a service manager loads the environment.

### 6. Test the configuration

```bash
.venv/bin/python zabbix_to_oxidized.py --dry-run
```

The generated `router.db` content is written to standard output. Status messages and warnings are written to standard error.

### 7. Run the exporter

```bash
.venv/bin/python zabbix_to_oxidized.py
```

On success, the configured `router.db` is created or updated atomically.



## Zabbix Host Configuration

Only active Zabbix hosts explicitly enabled for export are included.

### Required tags

| Tag | Example | Purpose |
|---|---|---|
| `oxidized_enabled` | `true` | Enables export for the host. |
| `oxidized_model` | `ios` | Defines the Oxidized model. |

Accepted case-insensitive values for `oxidized_enabled` are:

```text
1
true
yes
on
enabled
```

A host with a missing, empty, or false-like `oxidized_enabled` value is ignored. An enabled host without a valid model causes the export to fail unless `--skip-invalid` is used.

### Optional address override

| Tag | Example | Purpose |
|---|---|---|
| `oxidized_address` | `router01-mgmt.example.net` | Overrides automatic interface selection. |

Use this tag when Oxidized must connect through a management network, NAT address, dedicated DNS name, or any address different from the one used for Zabbix monitoring.

### Model format

The model is converted to lowercase and may contain only `a-z`, `0-9`, `.`, `-`, and `_`.

Valid examples:

```text
ios
iosxr
junos
routeros
vendor_model
vendor-model
vendor.model
```

The exporter validates syntax only. It does not verify that the installed Oxidized version supports the specified model.

### Duplicate tag values

Repeated identical values are accepted. Multiple different non-empty values for the same relevant tag are ambiguous and cause an error. For example:

```text
oxidized_model=ios
oxidized_model=iosxr
```

## Address Selection

When `oxidized_address` is absent, interfaces are checked in this order:

1. Main SNMP interface
2. Additional SNMP interface
3. Main Zabbix Agent interface
4. Additional Zabbix Agent interface
5. Main interface of another type
6. Additional interface of another type

For each interface, the exporter follows the Zabbix `useip` setting:

- `useip=1`: use the `ip` field.
- `useip=0`: use the `dns` field.

If an interface has no usable address, the next interface is checked.

## Output Format

The default format is:

```text
name:address:model
```

Change the one-character delimiter with `--delimiter`:

```bash
python zabbix_to_oxidized.py --delimiter ';'
```

Output:

```text
router01;10.10.10.1;ios
router02;router02.example.net;junos
```

The exporter and the Oxidized CSV source must use the same delimiter.

> IPv6 addresses contain colons and therefore conflict with the default `:` delimiter. Use a different Oxidized-compatible delimiter or a DNS name for IPv6 devices.

## Detailed Functionality

### API communication and authentication

The exporter sends a Zabbix `host.get` JSON-RPC request for active hosts (`status=0`) and retrieves technical names, interfaces, and tags.

The URL may be a base URL:

```text
https://zabbix.example.net
```

or the complete API endpoint:

```text
https://zabbix.example.net/api_jsonrpc.php
```

Bearer authentication is used by default:

```text
Authorization: Bearer <token>
```

For environments requiring the JSON-RPC `auth` field:

```bash
python zabbix_to_oxidized.py --auth-mode jsonrpc
```

### TLS verification

The system CA store is used by default. To use an internal CA:

```bash
python zabbix_to_oxidized.py \
  --ca-file /etc/ssl/certs/company-ca.pem
```

For temporary diagnostics only:

```bash
python zabbix_to_oxidized.py --insecure
```

Disabling certificate validation weakens connection security. A valid system or custom CA should be used in production.

### Validation

Before writing a device, the exporter verifies that:

- The technical host name, address, and model are not empty.
- No field contains the configured delimiter.
- No field contains carriage returns or line feeds.
- The model uses only permitted characters.
- No duplicate technical host names exist.

By default, one invalid enabled host stops the export, preventing an incomplete inventory from replacing a valid file. To skip invalid hosts and print warnings:

```bash
python zabbix_to_oxidized.py --skip-invalid
```

Use this option carefully because skipped devices are absent from the generated file.

### Concurrent-run protection

An exclusive non-blocking lock is acquired on:

```text
router.db.lock
```

A second exporter process using the same destination exits instead of writing concurrently. The lock file may remain after execution; synchronization depends on the active operating-system lock, not on the file's presence.

### Atomic replacement

The destination is never written directly. The exporter:

1. Creates a temporary file in the destination directory.
2. Writes the complete generated content.
3. Flushes buffers and synchronizes file data.
4. Applies previous ownership and permissions where possible.
5. Atomically replaces the destination.
6. Synchronizes the directory entry.

Readers therefore normally see either the complete old file or the complete new file, not a partially written file. A newly created destination receives `0640` permissions.

### Backups

Before replacing an existing file, the exporter creates:

```text
router.db.bak
```

It contains the previous version and is overwritten on the next changed export. Disable backups with:

```bash
python zabbix_to_oxidized.py --no-backup
```

Atomic replacement remains active.

### Unchanged content

If the generated content is identical to the current content, `router.db` is not rewritten. This preserves its modification time and avoids unnecessary downstream work.

The destination is treated as fully generated content. Manual comments or extra lines are not preserved during an update.

### Safety checks

The minimum-host check prevents a suspiciously small inventory from being written:

```bash
python zabbix_to_oxidized.py --min-hosts 50
```

The default minimum is `1`.

When an existing file is present, the exporter also compares device counts. A decrease of more than 15 percent is rejected by default:

```bash
python zabbix_to_oxidized.py --max-drop-percent 15
```

After reviewing the dry-run output, an intentional reduction can be applied with:

```bash
python zabbix_to_oxidized.py --force
```

`--force` disables only percentage-drop protection. It does not disable validation, duplicate detection, API error handling, or the minimum-host requirement.

<details>
<summary><strong>General Program Logic</strong></summary>

1. Parse command-line arguments and supported environment variables.
2. Verify that the Zabbix URL and API token are configured.
3. Validate basic settings, including the output delimiter.
4. Configure TLS certificate verification.
5. Create the destination directory if necessary.
6. Acquire an exclusive non-blocking file lock.
7. Create the Zabbix API client and configure authentication.
8. Call `host.get` for enabled hosts, interfaces, and tags.
9. Check `oxidized_enabled` for every returned host.
10. Ignore hosts that are not explicitly enabled for export.
11. Read and normalize `oxidized_model`.
12. Use `oxidized_address` when an override exists.
13. Otherwise, select the best available host interface.
14. Validate the technical name, address, and model.
15. Stop on invalid data, or skip the host with `--skip-invalid`.
16. Sort valid devices by technical host name.
17. Reject duplicate names.
18. Render each device using `name:address:model` or the selected delimiter.
19. In dry-run mode, print the result without replacing `router.db`.
20. Read the existing file and count its device entries.
21. Enforce the minimum-host requirement.
22. Reject an unexpectedly large device-count reduction unless forced.
23. Exit without rewriting when the content is unchanged.
24. Create a backup unless `--no-backup` is specified.
25. Write and synchronize a temporary file.
26. Preserve previous ownership and permissions where possible.
27. Atomically replace `router.db` and synchronize its directory.
28. Report the number of exported devices and release the lock.

</details>

## Command-Line Options

View the exact options supported by the installed version:

```bash
python zabbix_to_oxidized.py --help
```

| Option | Description |
|---|---|
| `--url URL` | Zabbix base URL or API endpoint. |
| `--token TOKEN` | API token; prefer `ZABBIX_TOKEN`. |
| `--auth-mode {bearer,jsonrpc}` | Token transport mode. |
| `--output PATH` | Destination file path. |
| `--enabled-tag TAG` | Tag that enables export. |
| `--model-tag TAG` | Tag containing the Oxidized model. |
| `--address-tag TAG` | Optional management-address tag. |
| `--delimiter CHAR` | One-character output delimiter. |
| `--timeout SECONDS` | API timeout. |
| `--min-hosts NUMBER` | Minimum required device count. |
| `--max-drop-percent NUMBER` | Maximum permitted count decrease. |
| `--ca-file PATH` | Custom CA bundle. |
| `--insecure` | Disable TLS verification. |
| `--dry-run` | Print output without replacing the file. |
| `--skip-invalid` | Skip invalid enabled hosts. |
| `--force` | Allow a reduction beyond the configured limit. |
| `--no-backup` | Disable creation of `router.db.bak`. |

## Examples

Preview output:

```bash
python zabbix_to_oxidized.py --dry-run
```

Save preview output while leaving diagnostics on the terminal:

```bash
python zabbix_to_oxidized.py --dry-run > router.db.preview
```

Use custom tag names:

```bash
python zabbix_to_oxidized.py \
  --enabled-tag backup_enabled \
  --model-tag backup_model \
  --address-tag backup_address
```

Require at least 100 devices and permit a maximum 10 percent decrease:

```bash
python zabbix_to_oxidized.py \
  --min-hosts 100 \
  --max-drop-percent 10
```

## Recommended Deployment Practices

- Use a dedicated read-only Zabbix API token.
- Keep the token in a protected `.env` file, not in shell history.
- Use HTTPS with certificate validation.
- Run `--dry-run` before initial deployment and after tag changes.
- Configure a meaningful `--min-hosts` value.
- Keep count-reduction protection enabled for unattended runs.
- Run the exporter as the owner of `router.db` where practical.
- Ensure the Oxidized process can read the generated file.
- Treat `router.db` as generated content.
- Monitor non-zero exit codes and standard-error output.

## Exit Codes

| Code | Meaning |
|---:|---|
| `0` | Success, including dry-run and unchanged-content cases. |
| `1` | Expected exporter error, such as an API, validation, or safety-check failure. |
| `130` | Interrupted with `Ctrl+C` or `SIGINT`. |

Unexpected operating-system or filesystem failures may produce a traceback and another non-zero code.

## Troubleshooting

### No devices are exported

Check that the token can read the intended hosts, the hosts are enabled in Zabbix, `oxidized_enabled` contains an accepted true value, every selected host has `oxidized_model`, and a usable interface or address override exists.

```bash
python zabbix_to_oxidized.py --dry-run
```

### TLS certificate errors

```bash
python zabbix_to_oxidized.py \
  --ca-file /path/to/internal-ca.pem
```

Use `--insecure` only to confirm that certificate validation is the cause.

### The destination is not updated

Check whether the generated content is unchanged, a safety threshold failed, another process holds the lock, or the process cannot write to the destination directory.

### Oxidized cannot parse the file

Confirm that Oxidized uses the same delimiter and expected field order, IPv6 addresses are not combined with the default colon delimiter, and every model is supported by the installed Oxidized version.

## Security Notes

The Zabbix API token is a secret. Do not commit it, log it, or pass it through shared shell history. Keep `.env` readable only by the account that runs the exporter. Use HTTPS with certificate validation in production.
