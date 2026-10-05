import getpass
import grp
import os
import sys
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8", errors="surrogateescape")

path = Path('/etc/bttn/bttn.env')
values = {}
for line in path.read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        name, value = line.split('=', 1)
        values[name] = value.strip().strip('"')
values.pop('OPENROUTER_API_KEY', None)
values.pop('OPENROUTER_MODEL', None)
values['SENDER_EMAIL'] = 'research.treasury2025@gmail.com'
values.setdefault('ZENMUX_MODEL', 'google/gemini-3.8-flash')
print('BTTN configuration. Secret input is hidden. Enter keeps the existing value.')
for name, label, secret in [
    ('GEMINI_API_KEY', 'Gemini API key', True),
    ('ZENMUX_API_KEY', 'ZenMux API key', True),
    ('SENDER_PASSWORD', 'Gmail App Password (not your normal password)', True),
    ('RECIPIENTS', 'Official recipient email addresses, comma separated', False),
]:
    while True:
        try:
            value = (getpass.getpass(label + ': ') if secret else input(label + ': ')).strip()
        except UnicodeError:
            print('Invalid input encoding. Switch keyboard to ENG and enter this field again.')
            continue
        if value and (not value.isascii() or any(ord(ch) < 32 or ch in ['"', chr(92)] for ch in value)):
            print('Use English letters/numbers only. Switch keyboard to ENG and enter this field again.')
            continue
        if value:
            values[name] = value.replace(" ", "") if name == "SENDER_PASSWORD" else value
        break
os.umask(0o077)
with path.open('w') as f:
    for name, value in values.items():
        f.write(name + '="' + value + '"\n')
os.chown(path, 0, grp.getgrnam('bttn').gr_gid)
os.chmod(path, 0o640)
print('Saved. No email sent. Publication timer remains unchanged.')
