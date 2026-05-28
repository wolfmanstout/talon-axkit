from talon import Context, Module

ctx = Context()
ctx.matches = r"""
os: mac
app: chrome
title: / - Chat/
"""
mod = Module()


@ctx.action_class("user")
class Actions:
    def accessibility_adjust_context_for_application(el, context):
        if context.content is None:
            return context

        # Google Chat represents emoji in the compose box as "\n\n" in AXValue,
        # but AXSelectedTextRange offsets skip those placeholders. Real blank
        # lines can also produce "\n\n", so fall back instead of guessing.
        if "\n\n" in context.content:
            context.content = None

        return context
