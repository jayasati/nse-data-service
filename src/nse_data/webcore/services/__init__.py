"""Business logic — orchestrates repositories + transforms into the JSON shapes
surfaces expect. Framework-agnostic: raises webcore.errors that route layers
map to HTTP status codes. One module per bounded context, mirroring
repositories/.
"""
