def register(ctx):
    from .adapter import register as register_adapter

    return register_adapter(ctx)

__all__ = ["register"]
