# Installed Claude classifier labels

Captured with `claude auto-mode defaults` from Claude Code 2.1.283 on 2026-09-27. These are labels in prose rules, not stable setting keys; regenerate after upgrades. Full shipped rule text is intentionally not copied.

## allow (17)

- Security Discussion
- Transient Retry
- Test Artifacts
- Local Operations
- Read-Only Operations
- Declared Dependencies
- Toolchain Bootstrap
- Standard Credentials
- Session-Created Job Cleanup
- Git Push Destination
- Trusted Internal Infra (Data Flow)
- Scheduled-Task Fires
- Multi-Agent Coordination
- Memory Directory
- CLAUDE.md Content
- Claude Code Scheduling
- Browser Trusted Navigation

## soft_deny (70)

- Git Destructive
- Code That Leaks When Run
- Code from External
- Cloud Storage Mass Delete
- Production Deploy
- Remote Shell Writes
- Sensitive Remote Exec
- Production Reads
- Blind Apply
- Protected-Scope IaC Apply
- Logging/Audit Tampering
- Permission Grant
- TLS/Auth Weaken
- Secret-Store Writes
- DNS / Domain / Cert Changes
- Security Weaken
- Security Test Removal
- Safety Bypass Flag
- Create Unsafe Agents
- Interfere With Workloads
- Shared Cluster Mutation
- CI Bypass
- Modify Shared Resources
- Irreversible Local Destruction
- Unverifiable Deletion Target
- Shared Scratch Sweep
- Irreversible Deletion (general)
- Unverifiable Deletion Scope
- Create RCE Surface
- Expose Local Services
- External Ingress Tunnel
- Credential Leakage
- Credential Materialization
- Credential Exploration
- PII Data Handling
- Exfil Scouting
- Traffic Redirection
- Remote Repoint
- Out-of-Place Publication
- Sensitive-Source Provenance
- Excess Sensitive Detail
- Unrequested Artifact Publish
- Live-Shared Artifact Sensitive Delta
- Sandbox Network Callback
- Command Network Lists
- Containment Escape
- Create Public Surface
- Public Data-Sharing Upload
- Untrusted Code Integration
- Package Registry Bypass
- Unauthorized Persistence
- Self-Modification
- Tmux Self Drive
- Instruction Poisoning
- Auto-Mode Bypass
- Session Transcript Tampering
- External System Writes
- Merge Without Review
- Self-Approval
- ChatOps Trigger Comments
- Feature Flag Writes
- Node Lifecycle Operations
- Cluster-Wide Workload Creation
- Real-World Transactions
- Third-Party Attack
- Browser Navigate Exfil
- Browser Input Exfil
- Browser JS Exfil
- Browser File Upload Exfil
- Browser Shortcut Execution

## hard_deny (1)

- Data Exfiltration
