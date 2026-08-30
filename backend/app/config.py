"""配置管理模块"""

from typing import List
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# 加载当前目录的 .env
load_dotenv()

# LLM配置字段在环境变量留空时回退的默认值
_LLM_FIELD_DEFAULTS = {
    "llm_model": "gpt-4o",
    "llm_temperature": 0.7,
    "llm_timeout": 60,
    "llm_day_timeout": 45,
}


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # 控制是否环境变量匹配字段时是否区分大小写。
        extra="ignore",  # 忽略未声明的环境变量
        ignore_file=".gitignore",  # 明确加载 .gitignore 作为忽略源, 防止 pydantic-settings 在 git 仓库里静默忽略被 gitignore 的 .env
    )

    # 应用基本配置
    app_name: str = "LangChain智能旅行助手"
    app_version: str = "1.0.0"
    app_env: str = "development"
    debug: bool = False

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000

    # 本地默认 SQLite；生产可设置 DATABASE_URL 为 PostgreSQL 等 SQLAlchemy 连接串。
    # 不配置时保留零依赖、可离线启动的本地体验。
    database_url: str = Field(default="")

    # CORS配置 - 使用字符串,在代码中分割
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"

    # 高德地图API配置
    amap_api_key: str = ""

    # LLM配置 (LangChain ChatOpenAI, 兼容任意OpenAI格式端点)
    # 优先读取 LLM_* 命名, 同时兼容 OPENAI_* 旧命名
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY"),  # 多别名备选，按顺序寻找环境变量，优先去找环境变量 LLM_API_KEY如果找不到 LLM_API_KEY，自动退而求其次读取 OPENAI_API_KEY
    )
    llm_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_BASE_URL", "OPENAI_BASE_URL"),
    )
    llm_model: str = Field(
        default="gpt-4o",
        validation_alias=AliasChoices(
            "LLM_MODEL_ID", "LLM_MODEL", "OPENAI_MODEL", "OPENAI_MODEL_NAME"
        ),
    )
    llm_temperature: float = 0.7
    llm_timeout: int = 60
    # 单日草稿的硬上限。取值会与全局 LLM_TIMEOUT 取较小者，超时立即返回真实 POI 兜底。
    llm_day_timeout: int = Field(default=45, ge=5, le=120)
    # 逐日生成时的并发数。本机演示默认同时生成最多四天；可按模型供应商限流下调。
    llm_concurrency: int = Field(default=4)
    # 单日 JSON 只包含 2-3 个景点和三餐；限制输出避免模型生成冗长文本拖慢响应。
    llm_day_max_tokens: int = Field(default=1800, ge=800, le=4096)

    # RAG 嵌入模型配置 (OpenAI 兼容接口 / 中转, 与 LLM 同一中转服务)
    # 复用 LLM_BASE_URL/LLM_API_KEY, 也可单独用 EMBEDDING_BASE_URL/EMBEDDING_API_KEY 覆盖;
    # 未配置时 RAG 功能自动降级禁用, 不影响旅行规划主流程
    embedding_model: str = Field(default="text-embedding-v4")
    embedding_base_url: str = Field(default="")
    embedding_api_key: str = Field(default="")

    # 多模态知识解析使用独立模型；缺省复用 LLM 的 key/base_url，避免影响行程生成模型。
    vision_model: str = Field(default="", validation_alias=AliasChoices("VISION_MODEL_ID", "VISION_MODEL"))
    vision_base_url: str = Field(default="")
    vision_api_key: str = Field(default="")
    vision_timeout: int = Field(default=60, ge=10, le=180)
    # 启动时将已存在的该用户名授予审核权限；空值表示不自动授予任何管理员。
    bootstrap_admin_username: str = Field(default="")

    # 接口鉴权 (JWT)
    # 生产环境务必设置强随机 SECRET_KEY (可: python -c "import secrets; print(secrets.token_urlsafe(48))")
    jwt_secret_key: str = Field(default="dev-secret-change-me")
    jwt_algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=60 * 24)  # token 有效期, 默认24小时

    # 日志配置
    log_level: str = "INFO"

    
    # @field_validator：Pydantic 字段校验钩子
    # 在环境变量赋值给类字段之前 / 之后，拦截值，自定义处理逻辑。
    @field_validator("llm_model", "llm_temperature", "llm_timeout", "llm_day_timeout", mode="before")
    @classmethod
    def _empty_env_to_default(cls, v, info):
        """环境变量为空字符串时回退到默认值,避免覆盖默认配置"""
        if v == "" or v is None:
            return _LLM_FIELD_DEFAULTS.get(info.field_name)
        return v

    def get_cors_origins_list(self) -> List[str]:
        """获取CORS origins列表"""
        return [origin.strip() for origin in self.cors_origins.split(",")]


# 创建全局配置实例
settings = Settings()


def get_settings() -> Settings:
    """获取配置实例"""
    return settings


# 验证必要的配置
def validate_config(config: Settings | None = None):
    """验证配置是否完整"""
    active_settings = config or settings
    errors = []
    warnings = []

    if not active_settings.amap_api_key:
        errors.append("AMAP_API_KEY未配置")

    if not active_settings.llm_api_key:
        message = "LLM_API_KEY或OPENAI_API_KEY未配置,LLM功能可能无法使用"
        if active_settings.app_env.lower() == "production":
            errors.append(message)
        else:
            warnings.append(message)

    jwt_is_weak = active_settings.jwt_secret_key in ("dev-secret-change-me", "") or len(active_settings.jwt_secret_key) < 32
    if jwt_is_weak:
        message = (
            "JWT_SECRET_KEY 使用默认值或长度不足32字符，生产环境必须设置强随机值 "
            "(python -c \"import secrets; print(secrets.token_urlsafe(48))\")"
        )
        if active_settings.app_env.lower() == "production":
            errors.append(message)
        else:
            warnings.append(message)

    if errors:
        error_msg = "配置错误:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_msg)

    if warnings:
        print("\n⚠️  配置警告:")
        for w in warnings:
            print(f"  - {w}")

    return True


# 打印配置信息(用于调试)
def print_config():
    """打印当前配置(隐藏敏感信息)"""
    print(f"应用名称: {settings.app_name}")
    print(f"运行环境: {settings.app_env}")
    print(f"版本: {settings.app_version}")
    print(f"服务器: {settings.host}:{settings.port}")
    print(f"数据库: {'外部 DATABASE_URL' if settings.database_url else '本地 SQLite'}")
    print(f"高德地图API Key: {'已配置' if settings.amap_api_key else '未配置'}")

    print(f"LLM API Key: {'已配置' if settings.llm_api_key else '未配置'}")
    print(f"LLM Base URL: {settings.llm_base_url or 'https://api.openai.com/v1 (官方默认)'}")
    print(f"LLM Model: {settings.llm_model}")
    print(f"LLM Temperature: {settings.llm_temperature}")
    print(f"LLM Timeout: {settings.llm_timeout}s")
    print(f"单日 LLM Timeout: {settings.llm_day_timeout}s")
    embedding_api_key = settings.embedding_api_key or settings.llm_api_key
    embedding_base_url = settings.embedding_base_url or settings.llm_base_url or "https://api.openai.com/v1"
    print(f"RAG 嵌入模型: {settings.embedding_model} @ {embedding_base_url} ({'已配置(走中转)' if embedding_api_key else '未配置(自动禁用)'})")
    print(f"视觉解析模型: {settings.vision_model or '未配置'}")
    print(f"日志级别: {settings.log_level}")


if __name__ == "__main__":
    print("当前配置:")
    settings = Settings()
    print_config()
