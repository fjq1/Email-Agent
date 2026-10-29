"""导航栏只读取平台名称和模型配置状态，不查询模型服务。"""

from email_agent.services.model_selection import model_selection_state


def model_selector(request):
    if not request.user.is_authenticated:
        return {}
    return {"model_selector": model_selection_state(request.user)}
