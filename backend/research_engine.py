"""
Derin Araştırma Motoru
-----------------------
Tek bir küçük (10B altı) Hugging Face modeliyle çalışacak şekilde tasarlandı.
Varsayılan model: Qwen/Qwen2.5-7B-Instruct

Akış:
  1) plan_aspects()      -> konuyu 5-7 alt araştırma başlığına böler
  2) search_web()         -> her başlık için DuckDuckGo'da arama yapar (key gerekmez)
  3) fetch_page_text()    -> bulunan sayfaların gerçek metnini çeker
  4) summarize_aspect()   -> o başlığı kaynaklardan özetler
  5) synthesize_report()  -> tüm özetleri birleştirip nihai raporu yazar
"""

import os
import json
import re
from typing import List, Dict, Generator

from huggingface_hub import InferenceClient
from ddgs import DDGS
import trafilatura

MODEL = os.environ.get("HF_MODEL", "Qwen/Qwen2.5-7B-Instruct")
HF_TOKEN = os.environ.get("HF_TOKEN")

MAX_ASPECTS = 7
MAX_SOURCES_PER_ASPECT = 4
MAX_CHARS_PER_SOURCE = 2500   # 7B modelin bağlamını boğmamak için kısıtlıyoruz
MAX_TOKENS_SMALL_CALL = 700
MAX_TOKENS_FINAL_CALL = 1600


def get_client() -> InferenceClient:
    if not HF_TOKEN:
        raise RuntimeError(
            "HF_TOKEN ayarlanmamış. .env dosyasına Hugging Face API anahtarınızı ekleyin."
        )
    return InferenceClient(model=MODEL, token=HF_TOKEN, provider=os.environ.get("HF_PROVIDER", "auto"))


def _chat(client: InferenceClient, system: str, user: str, max_tokens: int) -> str:
    resp = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.4,
    )
    return resp.choices[0].message.content.strip()


def _extract_json_list(text: str) -> List[Dict]:
    """Model bazen JSON'un etrafına açıklama ekleyebilir; sadece [...] kısmını çekeriz."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    raw = match.group(0) if match else text
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Son çare: modelin ürettiği başlıkları satır satır yakala
        lines = [l.strip("-• ").strip() for l in text.splitlines() if l.strip()]
        return [{"title": l, "query": l} for l in lines[:MAX_ASPECTS]]


def plan_aspects(client: InferenceClient, topic: str) -> List[Dict]:
    system = """Sen bir derin arastirma planlayicisisin. Gorevin, SANA VERILEN KONUYA OZGU 5-7 alt arastirma basligi uretmek. Basliklar tamamen konunun turune gore degisir; asagidaki ornekler SADECE farkli konu turlerinde basliklarin ne kadar FARKLI olabilecegini gostermek icindir, bunlari kopyalama:

- Bir suc/fail olayiysa: kurbanlar, deliller, yazilan kitaplar, medyada yer alisi, supheliler, kronoloji.
- Bir sirketse: kurulus hikayesi, kurucular, finansman/yatirimlar, urunler, rakipler, tartismalar/krizler.
- Bir tarihi olaysa: nedenleri, taraflar/aktorler, onemli anlar, sonuclari, tarihsel yorumlar/tartismalar.
- Bir bilim insani/kisiyse: hayati, katkilari/kesifleri, tartismali yonleri, etkisi/mirasi, iliskili kisiler.

Bu dort ornek de birbirinden tamamen farkli kategoriler kullaniyor, cunku her biri kendi konusuna ozgu. SANA VERILEN KONU hangi turdense, basliklari SIFIRDAN o ture gore uret, yukaridaki orneklerin hicbirini dogrudan kullanma, sadece ilham al. Konu bir suc olayi DEGILSE 'kurban', 'supheli', 'delil' gibi kelimeler KESINLIKLE kullanma.

SADECE su formatta bir JSON listesi dondur, baska hicbir aciklama ekleme:
[{"title": "Kisa baslik", "query": "internette aratilacak arama sorgusu"}, ...]"""
    user = f"Konu: {topic}\nBu konu için 5-7 alt araştırma başlığı üret."
    raw = _chat(client, system, user, max_tokens=MAX_TOKENS_SMALL_CALL)
    aspects = _extract_json_list(raw)
    return aspects[:MAX_ASPECTS]


def search_web(query: str, max_results: int = MAX_SOURCES_PER_ASPECT) -> List[Dict]:
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results, region="tr-tr"):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
    except Exception as e:
        results.append({"title": "Arama hatası", "url": "", "snippet": str(e)})
    return results


def fetch_page_text(url: str) -> str:
    try:
        downloaded = trafilatura.fetch_url(url, timeout=10)
        if not downloaded:
            return ""
        text = trafilatura.extract(downloaded) or ""
        return text[:MAX_CHARS_PER_SOURCE]
    except Exception:
        return ""


def summarize_aspect(client: InferenceClient, topic: str, aspect: Dict, sources: List[Dict]) -> Dict:
    context_blocks = []
    for s in sources:
        body = s.get("full_text") or s.get("snippet") or ""
        if not body:
            continue
        context_blocks.append(f"KAYNAK: {s['title']} ({s['url']})\n{body}")
    context = "\n\n---\n\n".join(context_blocks) if context_blocks else "Kaynak bulunamadı."

    system = """Sen bir arastirma asistanisin. Sana bir konu, bir alt-arastirma basligi ve o basligi ile ilgili internetten toplanmis kaynak metinleri veriliyor. Gorevin SADECE bu kaynaklara dayanarak, o alt baslik icin 3-6 cumlelik, net ve dogru bir Turkce ozet yazmak. Kaynaklarda olmayan bilgiyi UYDURMA. Emin olmadigin yerlerde belirt. Sonunda kullandigin kaynaklarin basliklarini parantez icinde listele."""
    user = f"Ana konu: {topic}\nAlt başlık: {aspect['title']}\n\nKaynaklar:\n{context}"
    summary = _chat(client, system, user, max_tokens=MAX_TOKENS_SMALL_CALL)

    return {
        "title": aspect["title"],
        "summary": summary,
        "sources": [{"title": s["title"], "url": s["url"]} for s in sources if s.get("url")],
    }


def synthesize_report(client: InferenceClient, topic: str, aspect_results: List[Dict]) -> str:
    combined = "\n\n".join(
        f"### {a['title']}\n{a['summary']}" for a in aspect_results
    )
    system = """Sen bir bas arastirmacisin. Sana bir konu hakkinda farkli alt basliklardan derlenmis ozetler veriliyor. Gorevin bunlari birlestirip; tekrarlari kaldiran, celiskileri belirten, akici, iyi organize edilmis, Turkce kapsamli bir SONUC RAPORU yazmak. Rapor basliklar halinde olsun, ama alt basliklari kopyalamak yerine anlatiyi birlestir. En sonda 2-3 cumlelik genel bir degerlendirme/ozet paragrafi ekle."""
    user = f"Konu: {topic}\n\nAlt başlık özetleri:\n{combined}"
    return _chat(client, system, user, max_tokens=MAX_TOKENS_FINAL_CALL)


def run_research(topic: str) -> Generator[Dict, None, None]:
    """
    Her adımı sırayla üretir (generator) -> FastAPI tarafında streaming için kullanılır.
    Yield edilen sözlük tipleri: {"type": "plan"|"aspect"|"final"|"error", ...}
    """
    client = get_client()

    try:
        aspects = plan_aspects(client, topic)
    except Exception as e:
        yield {"type": "error", "message": f"Planlama hatası: {e}"}
        return

    yield {"type": "plan", "aspects": [a["title"] for a in aspects]}

    aspect_results = []
    for aspect in aspects:
        raw_sources = search_web(aspect.get("query", aspect["title"]))
        for s in raw_sources:
            if s.get("url"):
                s["full_text"] = fetch_page_text(s["url"])

        try:
            result = summarize_aspect(client, topic, aspect, raw_sources)
        except Exception as e:
            result = {"title": aspect["title"], "summary": f"Bu başlık özetlenemedi: {e}", "sources": []}

        aspect_results.append(result)
        yield {"type": "aspect", **result}

    try:
        final_report = synthesize_report(client, topic, aspect_results)
    except Exception as e:
        final_report = f"Final rapor oluşturulamadı: {e}"

    yield {"type": "final", "report": final_report}
