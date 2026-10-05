# Ubuntu VPS deployment

Production path: `/opt/bttn`; Python 3.12 virtual environment: `/opt/bttn/.venv`.
Run as the dedicated `bttn` account. Keep reports and delivery ledger in
`/var/lib/bttn/output` and `/var/lib/bttn/state` across updates.

Install Python venv, LibreOffice Writer, DejaVu/Liberation fonts and libgl1;
install `requirements-dev.txt` into the virtual environment. Run Ruff, pytest
and an offline fixture render before deployment.

Credentials belong only in `/etc/bttn/bttn.env`, owned by root:bttn, mode 0640.
Use `python3 deploy/configure.py` as root to enter Gemini/ZenMux keys and Gmail
App Password interactively. Never commit that environment file. Blank inputs
preserve existing values. ZenMux model availability also depends on account credit.

Copy the service and timer to `/etc/systemd/system/`, then run
`systemctl daemon-reload` and `systemd-analyze verify` on both files.
The timer triggers weekdays at 12:00 Asia/Ho_Chi_Minh, without catch-up after
reboots. A file lock prevents overlapping service invocations.

Do not enable publication until a live preview is validated and the operator
has authorized sending. Disable the GitHub BTTN midday workflow before enabling
the VPS timer; keep BTTN checks active. Ledgers are not shared between hosts.

Useful commands:

- Status: `systemctl status bttn.timer bttn.service`
- Logs: `journalctl -u bttn.service --no-pager`
- Stop future publications: `systemctl disable --now bttn.timer`
- Enable authorized publications: `systemctl enable --now bttn.timer`

A successful dry-run may still be `draft_with_issues`; inspect `manifest.json`
and validation files. Never delete the ledger to retry an uncertain email.
Keep the previous code revision for rollback; retain environment and state.
