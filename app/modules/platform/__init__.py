"""Active platform authoring services shared by the V4 production pipeline."""

__all__ = ["AuthoringService", "ChannelService", "ProjectService"]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from app.modules.platform.services import AuthoringService, ChannelService, ProjectService

    return {
        "AuthoringService": AuthoringService,
        "ChannelService": ChannelService,
        "ProjectService": ProjectService,
    }[name]
