"""Optional, session-scoped Star. Never called by tick, version checks or doctor."""

import json
import shutil
import subprocess

from .errors import LurkerError, require

REPO = "shishengkai/social-lurker"


def gh(*args):
    binary = shutil.which("gh")
    require(binary, "STAR_UNAVAILABLE", "没有已配置的 GitHub CLI")
    try:
        result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise LurkerError("STAR_UNAVAILABLE", "无法确认 GitHub 状态") from None
    return result


def state():
    result = gh("api", "user")
    require(result.returncode == 0, "STAR_UNAVAILABLE", "无法确认活动 GitHub 账号")
    user = json.loads(result.stdout).get("login")
    require(isinstance(user, str) and user, "STAR_UNAVAILABLE", "GitHub 账号未知")
    # GraphQL distinguishes an unstarred repo from an inaccessible/unknown 404.
    query = 'query { repository(owner:"shishengkai",name:"social-lurker") { viewerHasStarred } }'
    result = gh("api", "graphql", "-f", "query=" + query)
    require(result.returncode == 0, "STAR_UNAVAILABLE", "无法确认仓库 Star 状态")
    data = json.loads(result.stdout)
    require(not data.get("errors"), "STAR_UNAVAILABLE", "仓库状态查询失败")
    starred = data.get("data", {}).get("repository", {}).get("viewerHasStarred")
    require(type(starred) is bool, "STAR_UNAVAILABLE", "仓库 Star 状态未知")
    return user, starred


def invite(event):
    require(
        event in ("install_completed", "upgrade_completed"), "STAR_EVENT_REQUIRED", "仅成功安装或升级后可邀请"
    )
    try:
        user, starred = state()
        if starred:
            return None
        return {
            "account": user,
            "repository": REPO,
            "invitation": f"如果盯梢者对你有帮助，欢迎用 Star 支持项目。回复“确认”后，将使用当前 GitHub 账号 {user} 为 {REPO} 点 Star；也可忽略。",
        }
    except (LurkerError, ValueError, AttributeError):
        return None


def apply(account, confirmed):
    require(confirmed is True, "STAR_AUTHORIZATION_REQUIRED", "需要当前邀请对应的明确 Star 授权")
    user, starred = state()
    require(user == account, "STAR_ACCOUNT_CHANGED", "GitHub 账号已变化，原授权范围失效")
    if not starred:
        require(
            gh("api", "--method", "PUT", "user/starred/" + REPO).returncode == 0,
            "STAR_FAILED",
            "Star 未成功；安装升级结果不受影响",
        )
    verified_user, verified_star = state()
    require(verified_user == user and verified_star, "STAR_UNCONFIRMED", "Star 结果未能确认，不自动重试")
    return {"starred": True, "account": user, "repository": REPO}
