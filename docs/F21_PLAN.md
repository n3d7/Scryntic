# F21 / KER-25 — Model-worker isolation

Source: KER-25, ARCHITECTURE.md model-worker acceptance, completed F16/F20.
Base: main `3fdebbe3d19d55f0225240d3cb0056528239141a`.

1. Add one fixed Linux x86-64 CPU launcher using systemd system services,
   per-attempt DynamicUser, private PID/user/mount/network/IPC namespaces,
   selected read-only runtime/request mounts and private bounded tmpfs output.
   No shell, arbitrary executable, caller properties or elevated helper API.
2. Check actual identity, permissions, environment, descriptors, namespaces,
   cgroup and rlimit controls before invoking the pinned F20 fixture. Reuse
   F16 syscall policy without changing F16 resource defaults. Refuse unavailable
   controls. Keep F20 admission, attempt fencing and durable acceptance unchanged.
3. Import only bounded regular output via a pinned directory descriptor, then
   independently validate the canonical F20 response in the coordinator. Do not
   import native files or executable serialization. Kill/stop the whole unit on
   cancellation, deadline, protocol failure and shutdown before removing staging.
4. Add focused rejection, race, lifecycle and real-host qualification probes.
   Run normal gates, SonarQube, Semgrep and Trivy; validate attributable findings.
   Record unsupported host probes honestly and provide exact manual steps.
5. Document effective controls, TCB and residual limitations. Publish an unmerged
   PR. In Review requires successful required qualification; blocked validation
   remains In Progress with an explicit gap.

No real model, F22 conversion/evaluation, GPU qualification, deployment install,
generic privileged launcher, arbitrary unit configuration or execution authority.
