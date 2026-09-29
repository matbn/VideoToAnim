# Adicionar um novo backend de pose

Há dois caminhos. **Nenhum deles exige editar o núcleo ou o frontend.**

## Caminho A — plugin Python (recomendado)

1. Crie `plugins/meu_backend.py`:

```python
from typing import Sequence
import numpy as np
from core.adapter import AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17

class MeuBackend(ConfigurableAdapter):
    def __init__(self):
        cfg = AdapterConfig(
            name="meu_backend",              # id unico -> aparece no dropdown
            display_name="Meu Backend",
            native_layout=["j0", "j1", ...], # nomes das juntas na ordem NATIVA
            mapping={                        # nome nativo -> índice COCO-17
                "j0": COCO17.index("nose"),
                "j1": COCO17.index("left_shoulder"),
                # ...
            },
            coord="pixel",                   # "pixel" | "normalized"
            y_flip=False,                    # True se o backend usa y para cima
            score_map={"j0": 2, ...},        # opcional: índice do score por junta
            smoothing="none",                # "none" | "oneeuro"
            license="MIT",
            license_category="livre",       # livre | nao_comercial | licenca_a_parte
            notes="o que este detector tem de peculiar",
        )
        super().__init__(cfg)

    def is_available(self) -> bool:
        try:
            import minha_lib  # noqa
            return True
        except Exception:
            return False

    def availability_reason(self) -> str:
        return "requer 'pip install minha-lib'"

    def load(self, config: dict | None = None) -> None:
        self._model = ...  # carregar pesos aqui
        self._loaded = True

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        out = []
        for frame in frames:
            kp = ...    # (N, 2) na ordem de native_layout
            sc = ...    # (N,) scores em [0,1]  (ou None)
            out.append({"kp": kp, "score": sc} if kp is not None else None)
        return out

BACKEND = MeuBackend()   # <-- o registry procura por BACKEND ou BACKENDS
```

2. Reinicie o servidor (ou recarregue a página) e confirme:

```powershell
curl http://127.0.0.1:8000/api/backends
```

O `meu_backend` aparece na lista e no dropdown. Se for o primeiro backend **disponível** e o
preferido (`vitpose`) não estiver instalado, ele passa a ser o padrão efetivo.

3. Rode os testes de contrato — eles **iteram todos os backends registrados**,
   então o seu já entra automaticamente:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_adapter_contract.py tests/test_backend_matrix.py -q
```

> Dica: adicione o id do seu backend à lista `REGISTRABLE` de `tests/test_backend_matrix.py` para
> que ele também atravesse o pipeline completo no teste de matriz.

## Caminho B — descritor YAML com entrypoint

Se o backend já vive num pacote importável:

```yaml
# plugins/meu_backend.yaml
name: meu_backend
entrypoint: meu_pacote.modulo     # módulo que exponha BACKEND
metadata:
  license: MIT
```

## Peculiaridades que o Adapter resolve por configuração

| Peculiaridade | Como tratar |
|---|---|
| Ordem de juntas diferente | `native_layout` + `mapping` |
| Coordenadas normalizadas ([0,1]) | `coord="normalized"` (o Adapter multiplica por w/h) |
| y para cima (2D/3D) | `y_flip=True` (o Adapter converte para y-down) |
| Profundidade/aproximação da câmera | **cuidado com o sinal de Z**: nosso canônico usa **+Z = frente** (a T-pose tem os pés em +Z). Se o seu backend usa "z negativo = mais perto da câmera" (como o MediaPipe), inverta o sinal — ver `backends/mediapipe_backend.py::_world` |
| Score em canal separado | `score_map={junta_nativa: indice_do_score}` |
| Pontos ausentes | devolver `score` baixo; use `score_threshold` |
| Multi-pessoa | escolha a bbox/pessoa de maior confiança e devolva só ela (`multi_person=True` documenta) |
| Suavização embutida no modelo | deixar `smoothing="none"` para não suavizar duas vezes |
| Saída 3D nativa | preencher `extra={"kp3d": ...}` ou sobrescrever `kp3d` em `postprocess()` |

## Hooks do `ConfigurableAdapter`

- `infer_raw(frames, ctx)` — **obrigatório**: a chamada ao modelo.
- `postprocess(pose, index, ctx)` — opcional: ajustes por frame (ex.: injetar 3D).
- `is_available()` / `availability_reason()` — opcional: disponibilidade honesta.

## Convenções do esqueleto canônico (COCO-17)

`0 nose, 1 left_eye, 2 right_eye, 3 left_ear, 4 right_ear, 5 left_shoulder,
6 right_shoulder, 7 left_elbow, 8 right_elbow, 9 left_wrist, 10 right_wrist,
11 left_hip, 12 right_hip, 13 left_knee, 14 right_knee, 15 left_ankle,
16 right_ankle` — em **pixel absoluto**, **y para baixo**, `score` em [0,1].
No 3D: **metros**, **y para cima**, **+Z = frente** do personagem.

## Controlar se o backend aparece no dropdown

| atributo | efeito |
|---|---|
| `license_category` | `"livre"` / `"nao_comercial"` / `"licenca_a_parte"` (constantes `CAT_LIVRE`, `CAT_NAO_COMERCIAL`, `CAT_LICENCA_A_PARTE`). Aparece como rótulo colorido no dropdown, na API e no painel do job. Também alimenta `registry.by_license_category()`. |
| `license` | texto livre com a licença exata (ex.: `"AGPL-3.0 (Ultralytics)"`). Mostrado no hint ao selecionar. |
| `hidden_from_ui = True` | o backend continua **registrado** e executável, mas **não aparece** no dropdown. |
| `PLUGIN_DISABLED = True` (módulo) | o plugin **não é carregado**: existe no disco como exemplo, fora do registry, sem gerar erro. Estado do `plugins/example_backend.py`. |

No primeiro caso, `GET /api/backends?include_hidden=true` ainda o lista; no segundo, ele só volta a
existir quando `PLUGIN_DISABLED` for removido/False.
