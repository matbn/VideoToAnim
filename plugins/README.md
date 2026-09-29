# Plugins de backend (extensao sem tocar no nucleo)

Solte um arquivo aqui e ele aparece automaticamente no dropdown.

## Formato A — modulo Python (recomendado)

`plugins/meu_backend.py`:

```python
from core.adapter import AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17

class MeuBackend(ConfigurableAdapter):
    def __init__(self):
        cfg = AdapterConfig(
            name="meu_backend",                 # id unico (aparece no dropdown)
            display_name="Meu Backend",
            native_layout=list(COCO17),         # ordem dos keypoints nativos
            mapping={n: i for i, n in enumerate(COCO17)},  # nativo -> COCO-17
            coord="pixel",                      # "pixel" | "normalized"
        )
        super().__init__(cfg)

    def is_available(self) -> bool:
        return True

    def infer_raw(self, frames, ctx):
        # devolve, por frame: {"kp": (N,2), "score": (N,) | None} ou None
        ...

BACKEND = MeuBackend()
```

Reinicie o servidor (ou recarregue a pagina) e o backend aparece no dropdown.

## Formato B — YAML com entrypoint

`plugins/meu_backend.yaml`:

```yaml
name: meu_backend
entrypoint: meu_pacote.modulo        # modulo importavel que exponha BACKEND
metadata:
  license: MIT
```

## Regras

- `__init__.py` e arquivos começando com `_` sao ignorados.
- O `mapping` e o ponto central: e nele que voce descreve as peculiaridades do
  seu detector (ordem de juntas, coordenadas normalizadas/pixel, y invertido,
  score). Nada de editar o nucleo.
- Se `is_available()` retornar `False`, o backend aparece no dropdown marcado
  como indisponivel, com o motivo em `availability_reason()`.
