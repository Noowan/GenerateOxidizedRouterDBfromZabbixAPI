#!/usr/bin/env python3

import argparse
import fcntl
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
import requests


TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
MODEL_RE = re.compile(r"^[a-z0-9_.-]+$")


class ExportError(Exception):
    pass


class ZabbixAPI:
    def __init__(
        self,
        url,
        token,
        auth_mode="bearer",
        verify=True,
        timeout=30,
    ):
        if url.endswith("/api_jsonrpc.php"):
            self.url = url
        else:
            self.url = url.rstrip("/") + "/api_jsonrpc.php"

        self.token = token
        self.auth_mode = auth_mode
        self.verify = verify
        self.timeout = timeout
        self.request_id = 0

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json-rpc",
                "Accept": "application/json",
                "User-Agent": "zabbix-to-oxidized/1.0",
            }
        )

        if self.auth_mode == "bearer":
            self.session.headers["Authorization"] = f"Bearer {self.token}"

    def call(self, method, params):
        self.request_id += 1

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self.request_id,
        }

        # Для старых версий Zabbix можно передавать токен
        # в поле auth JSON-RPC-запроса.
        if self.auth_mode == "jsonrpc":
            payload["auth"] = self.token

        try:
            response = self.session.post(
                self.url,
                data=json.dumps(payload),
                timeout=self.timeout,
                verify=self.verify,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ExportError(f"Ошибка обращения к Zabbix API: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            text = response.text[:500]
            raise ExportError(
                f"Zabbix вернул не JSON. Начало ответа: {text!r}"
            ) from exc

        if "error" in data:
            error = data["error"]
            raise ExportError(
                "Ошибка Zabbix API: "
                f"code={error.get('code')}, "
                f"message={error.get('message')}, "
                f"data={error.get('data')}"
            )

        if "result" not in data:
            raise ExportError("В ответе Zabbix API отсутствует поле result")

        return data["result"]


def stderr(message):
    print(message, file=sys.stderr)


def get_unique_tag(tags, tag_name):
    values = []

    for tag in tags or []:
        if tag.get("tag") != tag_name:
            continue

        value = str(tag.get("value", "")).strip()

        if value and value not in values:
            values.append(value)

    if len(values) > 1:
        raise ExportError(
            f"Для тега {tag_name!r} обнаружено несколько разных значений: "
            f"{', '.join(values)}"
        )

    return values[0] if values else None


def interface_address(interface):
    use_ip = str(interface.get("useip", "1")) == "1"

    if use_ip:
        return str(interface.get("ip", "")).strip()

    return str(interface.get("dns", "")).strip()


def interface_priority(interface):
    interface_type = str(interface.get("type", ""))
    is_main = str(interface.get("main", "0")) == "1"

    # Типы интерфейсов Zabbix:
    # 1 — Agent
    # 2 — SNMP
    # 3 — IPMI
    # 4 — JMX
    if interface_type == "2" and is_main:
        return 0
    if interface_type == "2":
        return 1
    if interface_type == "1" and is_main:
        return 2
    if interface_type == "1":
        return 3
    if is_main:
        return 4
    return 5


def select_address(host, address_tag):
    tags = host.get("tags", [])

    override = get_unique_tag(tags, address_tag)
    if override:
        return override

    interfaces = sorted(
        host.get("interfaces", []),
        key=interface_priority,
    )

    for interface in interfaces:
        address = interface_address(interface)
        if address:
            return address

    return None


def validate_field(value, field_name, host_name, delimiter):
    if not value:
        raise ExportError(
            f"Хост {host_name!r}: поле {field_name!r} пустое"
        )

    if delimiter in value:
        raise ExportError(
            f"Хост {host_name!r}: поле {field_name!r} содержит "
            f"разделитель {delimiter!r}: {value!r}"
        )

    if "\n" in value or "\r" in value:
        raise ExportError(
            f"Хост {host_name!r}: поле {field_name!r} "
            "содержит перевод строки"
        )


def convert_hosts(
    hosts,
    enabled_tag,
    model_tag,
    address_tag,
    delimiter,
    skip_invalid,
):
    result = []
    errors = []

    for host in hosts:
        technical_name = str(host.get("host", "")).strip()
        tags = host.get("tags", [])

        try:
            enabled_value = get_unique_tag(tags, enabled_tag)

            if not enabled_value:
                continue

            if enabled_value.strip().lower() not in TRUE_VALUES:
                continue

            model = get_unique_tag(tags, model_tag)
            if model:
                model = model.strip().lower()

            address = select_address(host, address_tag)

            validate_field(
                technical_name,
                "name",
                technical_name or "<без имени>",
                delimiter,
            )
            validate_field(
                address,
                "address",
                technical_name,
                delimiter,
            )
            validate_field(
                model,
                "model",
                technical_name,
                delimiter,
            )

            if not MODEL_RE.fullmatch(model):
                raise ExportError(
                    f"Хост {technical_name!r}: недопустимое значение модели "
                    f"{model!r}. Разрешены a-z, 0-9, точка, дефис "
                    "и подчёркивание"
                )

            result.append(
                {
                    "name": technical_name,
                    "address": address,
                    "model": model,
                }
            )

        except ExportError as exc:
            if skip_invalid:
                errors.append(str(exc))
                continue
            raise

    result.sort(key=lambda item: item["name"].lower())

    names = set()
    duplicates = set()

    for item in result:
        if item["name"] in names:
            duplicates.add(item["name"])
        names.add(item["name"])

    if duplicates:
        raise ExportError(
            "Обнаружены повторяющиеся имена хостов: "
            + ", ".join(sorted(duplicates))
        )

    for error in errors:
        stderr(f"WARNING: {error}")

    return result


def count_existing_hosts(text):
    return len(
        [
            line
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    )


def render_router_db(devices, delimiter):
    lines = [
        delimiter.join(
            (
                device["name"],
                device["address"],
                device["model"],
            )
        )
        for device in devices
    ]

    return "\n".join(lines) + "\n"


def atomic_write(target, content, make_backup=True):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    previous_stat = None

    if target.exists():
        previous_stat = target.stat()

        if make_backup:
            backup = Path(str(target) + ".bak")
            shutil.copy2(target, backup)

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

        if previous_stat:
            os.chmod(temporary_name, previous_stat.st_mode & 0o777)

            try:
                os.chown(
                    temporary_name,
                    previous_stat.st_uid,
                    previous_stat.st_gid,
                )
            except PermissionError:
                stderr(
                    "WARNING: не удалось сохранить владельца router.db. "
                    "Запустите скрипт с достаточными правами."
                )
        else:
            os.chmod(temporary_name, 0o640)

        os.replace(temporary_name, target)

        # Синхронизируем запись каталога после атомарной замены.
        directory_fd = os.open(str(target.parent), os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def fetch_hosts(api):
    return api.call(
        "host.get",
        {
            "output": [
                "hostid",
                "host",
                "name",
                "status",
            ],
            "selectInterfaces": [
                "interfaceid",
                "type",
                "main",
                "useip",
                "ip",
                "dns",
                "port",
            ],
            "selectTags": [
                "tag",
                "value",
            ],
            "filter": {
                "status": "0",
            },
            "sortfield": "host",
            "sortorder": "ASC",
        },
    )


def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Выгрузка активных хостов из Zabbix API "
            "в router.db для Oxidized"
        )
    )

    parser.add_argument(
        "--url",
        default=os.getenv("ZABBIX_URL"),
        help="URL Zabbix, например https://zabbix.example.local",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("ZABBIX_TOKEN"),
        help="API-токен Zabbix; безопаснее передавать через ZABBIX_TOKEN",
    )
    parser.add_argument(
        "--auth-mode",
        choices=("bearer", "jsonrpc"),
        default=os.getenv("ZABBIX_AUTH_MODE", "bearer"),
        help="Способ передачи API-токена",
    )
    parser.add_argument(
        "--output",
        default=os.getenv(
            "ROUTER_DB",
            "/srv/oxidized/config/router.db",
        ),
        help="Путь к router.db на Docker-хосте",
    )
    parser.add_argument(
        "--enabled-tag",
        default=os.getenv(
            "OXIDIZED_ENABLED_TAG",
            "oxidized_enabled",
        ),
    )
    parser.add_argument(
        "--model-tag",
        default=os.getenv(
            "OXIDIZED_MODEL_TAG",
            "oxidized_model",
        ),
    )
    parser.add_argument(
        "--address-tag",
        default=os.getenv(
            "OXIDIZED_ADDRESS_TAG",
            "oxidized_address",
        ),
    )
    parser.add_argument(
        "--delimiter",
        default=os.getenv("OXIDIZED_DELIMITER", ":"),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("ZABBIX_TIMEOUT", "30")),
    )
    parser.add_argument(
        "--min-hosts",
        type=int,
        default=int(os.getenv("OXIDIZED_MIN_HOSTS", "1")),
        help="Минимально допустимое количество хостов",
    )
    parser.add_argument(
        "--max-drop-percent",
        type=float,
        default=float(
            os.getenv("OXIDIZED_MAX_DROP_PERCENT", "15")
        ),
        help=(
            "Максимально допустимое уменьшение количества хостов "
            "относительно текущего router.db"
        ),
    )
    parser.add_argument(
        "--ca-file",
        default=os.getenv("ZABBIX_CA_FILE"),
        help="Путь к внутреннему CA-сертификату",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Не проверять TLS-сертификат Zabbix",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Вывести результат, но не изменять router.db",
    )
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Пропускать некорректные хосты вместо остановки",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Игнорировать защиту от резкого уменьшения списка",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Не создавать router.db.bak",
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    if not args.url:
        raise ExportError(
            "Не задан URL Zabbix. Укажите --url или ZABBIX_URL"
        )

    if not args.token:
        raise ExportError(
            "Не задан API-токен. Укажите переменную ZABBIX_TOKEN"
        )

    if len(args.delimiter) != 1:
        raise ExportError("Разделитель должен состоять из одного символа")

    if args.insecure:
        verify = False
        requests.packages.urllib3.disable_warnings(
            requests.packages.urllib3.exceptions.InsecureRequestWarning
        )
    elif args.ca_file:
        verify = args.ca_file
    else:
        verify = True

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    lock_path = Path(str(output) + ".lock")

    with open(lock_path, "w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise ExportError(
                "Другой экземпляр экспортёра уже работает"
            ) from exc

        api = ZabbixAPI(
            url=args.url,
            token=args.token,
            auth_mode=args.auth_mode,
            verify=verify,
            timeout=args.timeout,
        )

        hosts = fetch_hosts(api)

        devices = convert_hosts(
            hosts=hosts,
            enabled_tag=args.enabled_tag,
            model_tag=args.model_tag,
            address_tag=args.address_tag,
            delimiter=args.delimiter,
            skip_invalid=args.skip_invalid,
        )

        if len(devices) < args.min_hosts:
            raise ExportError(
                f"Получено хостов: {len(devices)}; "
                f"минимально допустимо: {args.min_hosts}. "
                "router.db не изменён."
            )

        rendered = render_router_db(
            devices,
            args.delimiter,
        )

        if args.dry_run:
            print(rendered, end="")
            stderr(
                f"DRY-RUN: подготовлено устройств: {len(devices)}"
            )
            return 0

        old_text = ""

        if output.exists():
            old_text = output.read_text(encoding="utf-8")

        old_count = count_existing_hosts(old_text)
        new_count = len(devices)

        if old_count > 0 and new_count < old_count and not args.force:
            drop_percent = ((old_count - new_count) / old_count) * 100

            if drop_percent > args.max_drop_percent:
                raise ExportError(
                    f"Количество хостов уменьшилось с {old_count} "
                    f"до {new_count}, то есть на {drop_percent:.1f}%. "
                    f"Разрешено не более {args.max_drop_percent:.1f}%. "
                    "router.db не изменён. Для подтверждённого "
                    "изменения используйте --force."
                )

        if old_text == rendered:
            print(
                f"Изменений нет. В router.db устройств: {new_count}"
            )
            return 0

        atomic_write(
            output,
            rendered,
            make_backup=not args.no_backup,
        )

        print(
            f"router.db успешно обновлён: {output}; "
            f"устройств: {new_count}"
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ExportError as exc:
        stderr(f"ERROR: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        stderr("ERROR: выполнение прервано")
        sys.exit(130)