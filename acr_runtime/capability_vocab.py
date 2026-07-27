"""Closed Prompt 36 capability vocabulary shared by authorization boundaries."""

CAPABILITIES = frozenset({
    "network.read", "network.write",
    "filesystem.read", "filesystem.write",
    "shell.execute",
    "database.read", "database.write",
    "memory.read", "memory.write",
    "skill.create", "skill.activate",
    "agent.create", "credential.use",
})
