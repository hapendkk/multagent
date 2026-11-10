"""Роутинг запросов по моделям.

1. Генератор: простая задача -> лёгкая модель, сложная -> сильная (экономия
   VRAM и времени). Каскад: если лёгкая модель провалила проверку,
   доработку делает сильная (эскалация).
2. Валидатор: всегда модель ДРУГОГО семейства, чем генератор, чтобы
   не повторять те же ошибки и не «соглашаться с собой».
"""
import config


def family(model):
    base = model.split("/")[-1].split(":")[0].lower()
    for prefix, fam in config.MODEL_FAMILIES.items():
        if base.startswith(prefix):
            return fam
    return base


def route_generator(complexity, product_type):
    """Возвращает (модель, уровень, причина)."""
    if complexity is None:
        complexity = 50
    if product_type in config.STRONG_ONLY_TYPES:
        return config.GENERATOR_STRONG, "strong", f"тип «{product_type}» всегда на сильной модели"
    if complexity <= config.LIGHT_COMPLEXITY_MAX:
        return config.GENERATOR_LIGHT, "light", f"сложность {complexity} ≤ {config.LIGHT_COMPLEXITY_MAX}"
    return config.GENERATOR_STRONG, "strong", f"сложность {complexity} > {config.LIGHT_COMPLEXITY_MAX}"


def escalate(model, attempt):
    """Каскад: на доработке лёгкая модель заменяется сильной."""
    if config.ESCALATE_ON_FAIL and attempt > 1 and model != config.GENERATOR_STRONG:
        return config.GENERATOR_STRONG, True
    return model, False


def pick_validator(generator_model):
    """Первая модель-кандидат из семейства, отличного от генератора."""
    gen_family = family(generator_model)
    for m in config.VALIDATOR_CANDIDATES:
        if family(m) != gen_family:
            return m
    raise ValueError(f"нет валидатора другого семейства, чем {generator_model} ({gen_family})")
