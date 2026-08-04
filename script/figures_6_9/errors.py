class ReproductionError(RuntimeError):
    """Base error for the Figures 6-9 workflow."""


class ConfigError(ReproductionError):
    """Configuration is missing, malformed, or unsafe."""


class UnavailableError(ReproductionError):
    """A required external workload or device is unavailable."""


class ValidationError(ReproductionError):
    """Collected or supplied evidence failed an integrity gate."""
