from config.config_loader import load_config


default_config_file = "config.yaml"
config_file_valid = False


def check_config_file():
    global config_file_valid
    if config_file_valid:
        return
    """确认受版本控制的默认配置或本地覆盖可以正常加载。"""
    load_config()
    config_file_valid = True
