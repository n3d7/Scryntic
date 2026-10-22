# Local document and agent-instruction backups

Git exclusions control visibility, not durability. Shared project skills, historical plans, workflow guidance and runtime documentation are eligible for Git. Personal `AGENTS.md` files, filled handoffs (including `docs/HANDOFF.md`), task/audit reports, machine configuration, credentials, runtime state and generated data remain excluded. Eligibility does not stage, commit or publish a file.

## Installed workstation backup

The local helper is `$HOME/.local/libexec/scryntic-local-backup`. It runs as the current user through `scryntic-local-backup.service` and `scryntic-local-backup.timer`; no root service or Debian connection is involved. The installed instance is configured for this checkout.

- The timer runs every five minutes while the user systemd manager is available, with a persistent catch-up after downtime. It cannot create backups while the workstation/user manager is stopped.
- Snapshots live outside the checkout in `$HOME/.local/share/scryntic/local-backups/`, with private permissions. Each changed captured file set creates a new timestamped archive, archive checksum and per-file SHA-256 manifest. Unchanged content does not create another archive. Old snapshots are not automatically deleted or overwritten.
- Captured inputs are root project instructions/documentation and `.gitignore`; supported authored text files under `docs/`, `.agents/skills/` and `.local/`; documents/instructions in existing `.worktrees/*` checkouts and their top-level Markdown handoffs; and the explicitly inspected `.codex/f06-local-exclude` pattern file. This includes private task/audit reports and handoffs. Other `.codex` state is not captured. It is a document/instruction backup, not a full repository or trading-data backup.
- Credential directories, SSH keys, databases, model weights, logs, caches and runtime state are outside the selected inputs. Symlinks are not followed. Do not move credentials into documentation or skill resources.
- Files are fingerprinted before and after archive creation. An input changing during the snapshot fails the run rather than replacing a prior snapshot; the next scheduled run retries.

Check or trigger the installed service:

```sh
systemctl --user status scryntic-local-backup.timer
systemctl --user show scryntic-local-backup.service -p Result -p ExecMainStatus
systemctl --user start scryntic-local-backup.service
ls -lt "$HOME/.local/share/scryntic/local-backups/"
```

## Recovery

Select the appropriate timestamp; the newest snapshot may reflect a deliberate deletion, so earlier versions remain available. Verify and inspect an archive before restoring:

```sh
sha256sum --check "$HOME/.local/share/scryntic/local-backups/SNAPSHOT.tar.gz.sha256"
tar -tzf "$HOME/.local/share/scryntic/local-backups/SNAPSHOT.tar.gz"
tar -xOf "$HOME/.local/share/scryntic/local-backups/SNAPSHOT.tar.gz" AGENTS.md > /tmp/AGENTS.recovered.md
```

Replace `SNAPSHOT` with an actual timestamp. Compare the recovered copy before explicitly restoring it; do not extract over the whole checkout. Preserve any deliberately configured read-only/immutable protection on the live instructions. The backup does not automatically undo legitimate edits or deletions.

The copies survive checkout cleanup/reset because they are outside the repository. They remain on the same workstation: this does not protect against loss of the whole disk or home directory. Backup storage grows with changed versions and requires operator-managed retention.
