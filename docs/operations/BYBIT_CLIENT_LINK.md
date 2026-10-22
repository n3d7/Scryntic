# Debian client link to the separate Bybit broker

Scope authorized on 2026-10-19: prepare the existing Debian server for a laptop on the same LAN. This is **client transport preparation**, not delivery of a Bybit broker or PI01. No API credentials, pybit, broker code or account API requests belong on Debian. PI00/PI01 and their scientific gates are unchanged.

## Boundary and transport

Debian is an intentionally untrusted client with respect to laptop secrets: Codex has passwordless root there. Root can change this client and use its SSH identity. Security must therefore be enforced **on the laptop**, independently of this client configuration. Codex must have no administrative key, agent access, shell, file transfer, service control, sudo, container or hypervisor authority on the laptop.

The prepared system service uses a separate `scryntic-bybit-link` user and connects as the laptop's future `scryntic-bybit-tunnel` account. A single local Unix socket `/run/scryntic-bybit-link/broker.sock` forwards to laptop `127.0.0.1:8765`. Debian exposes no TCP listener. Socket directory mode 0750 and socket mask 0007 restrict access to the service and `scryntic-bybit-client` group; the selected existing Scryntic client account is `scryntic`. New logins/processes receive its new supplementary group. Root on Debian can bypass these local ACLs, but must still have only the laptop's restricted transport capability.

No password/agent fallback, automatic host-key enrolment or remote session is requested. A separate SSH identity is generated **on Debian**, never copied back as a private key and never committed. This is a restricted transport identity, not a Bybit API credential. The official pybit SDK and API `LoadCredential` material will belong only to the separately administered laptop broker.

## Installed preparation and pending activation

Source templates: [`ops/bybit-link`](../../ops/bybit-link). Debian paths:

- `/etc/scryntic/bybit-link/ssh_config` and `peer.conf.example`: root-managed, readable by the client group.
- `/etc/scryntic/bybit-link/known_hosts`: initially empty, root-managed. Only verified public laptop host-key material goes here.
- `/etc/scryntic/bybit-link/client_ed25519.pub`: public client key for the laptop administrator.
- `/var/lib/scryntic-bybit-link/id_ed25519`: private transport identity, mode 0600 under service-owned mode-0700 directory. Never read/export it.
- `/etc/systemd/system/scryntic-bybit-link.service`: installed, **not enabled**. Without `peer.conf` its start condition is false. An empty `known_hosts` also prevents SSH execution.

An established SSH tunnel does not establish broker health or API permissions. `ExitOnForwardFailure` checks forwarding setup, not availability of the eventual HTTP backend. Once enabled, connection attempts time out after 10 seconds; keepalives detect a dead connection and systemd retries every 30 seconds. No Scryntic collection/job service depends on this unit. A real connection, socket ACL check and disconnect/restart test remain pending the laptop.

## Laptop administrator handoff — do not execute from Codex's account

These are required settings for the future laptop account, **not a claim that it is provisioned**. The laptop administrator must independently install/configure SSH and its firewall for the Debian LAN address, verify effective settings with `sshd -t` and `sshd -T -C`, and confirm that all account authentication methods obey the restrictions.

The account `scryntic-bybit-tunnel` must have no sudo/admin groups, no usable password, a noninteractive shell, no writable broker/service/credential paths, and no access to the broker's systemd credential directory. A root-managed SSH configuration must apply:

```text
Match User scryntic-bybit-tunnel
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    PermitUserRC no
    MaxSessions 0
    AllowTcpForwarding local
    AllowStreamLocalForwarding no
    PermitOpen 127.0.0.1:8765
    PermitListen none
    PermitTunnel no
```

Use a root-managed authorized-key file outside the user's writable home. Its entry should additionally restrict the **public** Debian key:

```text
from="DEBIAN_LAN_IP",restrict,port-forwarding,permitopen="127.0.0.1:8765",command="/usr/bin/false" ssh-ed25519 PUBLIC_KEY_FROM_DEBIAN scryntic-bybit-link
```

The key options alone are insufficient: `AllowTcpForwarding local`, `AllowStreamLocalForwarding no`, `PermitListen none` and `MaxSessions 0` close alternate forwards and session/file-transfer channels. Validate the actual laptop OS/OpenSSH version; a locked account can affect public-key admission, so use the OS-supported account/PAM policy without enabling password login. Do not grant Codex temporary administrator access to work around this.

The laptop broker must independently enforce only wallet balance, positions and order history with bounded validated inputs, server-side authorization and safe output/logging. A tunnel is not an HTTP allowlist and does not protect an unrestricted pybit wrapper. Broker code, SDK qualification, mock API tests, systemd `LoadCredential` and manual Bybit key installation remain a separate laptop task. Do not install API keys before that review passes.

## Finish the link after the laptop is ready

1. Reserve stable LAN addresses. Obtain the laptop SSH host public key and fingerprint from its console/admin account. `ssh-keyscan` by itself does not authenticate a peer. Share only public keys/fingerprints, never private keys or Bybit credentials.
2. Laptop administrator authorizes `/etc/scryntic/bybit-link/client_ed25519.pub` with the restrictions above, and qualifies the broker with fake API responses before any real credentials.
3. On Debian, an administrator creates `/etc/scryntic/bybit-link/peer.conf` from the example with the verified private LAN IP/SSH port. Install the **verified** host public key in `known_hosts` under alias `scryntic-bybit-broker`. Both files remain root-owned and non-writable by the client group. Do not create an empty placeholder `peer.conf`.
4. Parse the effective client configuration offline, then start manually. Enable at boot only after end-to-end qualification:

```sh
sudo -u scryntic-bybit-link ssh -G -F /etc/scryntic/bybit-link/ssh_config scryntic-bybit-broker
sudo systemctl start scryntic-bybit-link.service
sudo systemctl status scryntic-bybit-link.service --no-pager
```

5. With a mocked laptop broker, verify socket owner/group/mode, deny another local user, accept only the selected client, deny shell/SFTP/other TCP and Unix forwards/reverse forwarding, reject unexpected/malformed HTTP operations, and test concurrent clients, disconnect/restart and host-key mismatch. No real authenticated Bybit request is part of these checks. Only then consider `sudo systemctl enable scryntic-bybit-link.service`.

Stop with `sudo systemctl stop scryntic-bybit-link.service`; remove boot enablement with `sudo systemctl disable scryntic-bybit-link.service`. Do not delete keys/evidence or remove existing Scryntic accounts/groups automatically.

## Evidence

Preparation inventory: Debian 13, OpenSSH 10.0p2 Debian-7+deb13u4, systemd 257.13, existing Python/curl/SSH tools. No packages need installation. No existing client-link paths or Scryntic system-unit files were found in the bounded inventory. The SSH daemon binary was not in the ordinary user's PATH; the existing remote login works and no sshd installation/configuration is changed.

Preparation completed at 2026-10-19 13:08 UTC. Remote retained source snapshot: `/srv/scryntic-lab/work/bybit-client-link.fh88aZpC`; installed metadata/evidence: `/etc/scryntic/bybit-link/preparation.json`. The transport public key is also available on the operator workstation as `/tmp/scryntic-bybit-link-client.pub` (public material only; copy to durable operator storage if needed). Its public fingerprint is `SHA256:mi71TtX4DDTNbUtPBzGfuGR0tS3yrO1A2vCMkSRU3Z0`.

Executed verification:

- `python3 ops/bybit-link/verify_config.py`: **6/6 PASS on workstation and Debian**, including final formatted source. A first template-existence check failed before the three templates were added, as expected. Mock peer configuration only: `ssh -G` does not connect, load a private key or call Bybit.
- `systemd-analyze verify`: PASS on installed Debian systemd 257, before and after installation.
- Controlled start without `peer.conf`: PASS, `ConditionResult=no`, `ExecMainPID=0`, `ActiveState=inactive`, `UnitFileState=disabled`, no Unix socket. No laptop/Bybit connection attempted.
- Installed template SHA-256 values match retained source. Service UID 999, noninteractive shell, no sudo grant; checking from the actual account also denies noninteractive sudo listing. Note: root's `sudo -l -U` can exit 0 while stating that the target user has no grant; its exit code alone is not evidence of privilege.
- Private transport-key mode 0600 and home mode 0700 verified from metadata; readability denied for `scryntic` and `nobody`. The private key was never read/exported. Configuration/known-host files root-owned 0640 under root-owned 0750 directory; unit root-owned 0644.
- Targeted Ruff lint and format checks: PASS. An initial import-order/format finding was corrected in the new verifier only. `git diff --check`: PASS.
- Independent read-only review of the five scoped source artifacts: no Important/Critical findings; it performed no deployment, connection, secret access or edits. This is configuration review, not qualification of the future laptop broker.
- No packages installed, existing SSH/firewall settings changed, or existing services restarted. No API credentials created/requested, real authenticated Bybit request, product-source change, Linear change, commit or push. Existing staged/unstaged work is preserved.

The laptop address, authenticated host-key pin, SSH server restrictions, real tunnel/socket ACLs, broker allowlist and API mocks, reconnect/restart/failure isolation across both hosts are **not yet qualified**. They are the next gate, not completed tests. The separately scoped PI00 scientific/data-rights blocker remains open.

References: [OpenSSH client forwarding/mask/host-key options](https://man.openbsd.org/ssh_config), [server account restrictions](https://man.openbsd.org/sshd_config), [systemd 257 unit conditions](https://www.freedesktop.org/software/systemd/man/257/systemd.unit.html), [systemd 257 execution/runtime-directory policy](https://www.freedesktop.org/software/systemd/man/257/systemd.exec.html). Context7 was consulted for OpenSSH/systemd; its systemd version listing was newer than this host, so installed parsers and version-257 manuals are the final compatibility checks.
