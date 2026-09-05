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
   system = (
    "Sen bir derin araştırma planlayıcısısın. Görevin, SANA VERİLEN KONUYA ÖZGÜ "
    "5-7 alt araştırma başlığı üretmek. Başlıklar tamamen konunun türüne göre değişir; "
    "aşağıdaki örnekler SADECE farklı konu türlerinde başlıkların ne kadar FARKLI "
    "olabileceğini göstermek içindir, bunları kopyalama:\n\n"
    "- Bir suç/fail olayıysa: kurbanlar, deliller, yazılan kitaplar, medyada yer alışı, "
    "şüpheliler, kronoloji.\n"
    "- Bir şirketse: kuruluş hikayesi, kurucular, finansman/yatırımlar, ürünler, "
    "rakipler, tartışmalar/krizler.\n"
    "- Bir tarihi olaysa: nedenleri, taraflar/aktörler, önemli anlar, sonuçları, "
    "tarihsel yorumlar/tartışmalar.\n"
    "- Bir bilim insanı/kişiyse: hayatı, katkıları/keşifleri, tartışmalı yönleri, "
    "etkisi/mirası, ilişkili kişiler.\n\n"
    "Bu dört örnek de birbirinden tamamen farklı kategoriler kullanıyor, çünkü her biri "
    "kendi konusuna özgü. SANA VERİLEN KONU hangi türdense, başlıkları SIFIRDAN o türe "
    "göre üret — yukarıdaki örneklerin hiçbirini doğrudan kullanma, sadece ilham al. "
    "Konu bir suç olayı DEĞİLSE 'kurban', 'şüpheli', 'delil' gibi kelimeler KESİNLİKLE "
    "kullanma.\n\n"
    "SADECE şu formatta bir JSON listesi döndür, başka hiçbir açıklama ekleme:\n"
    '[{"title": "Kısa başlık", "query": "internette aratılacak arama sorgusu"}, ...]'
)
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

    system = (
        "Sen bir araştırma asistanısın. Sana bir konu, bir alt-araştırma başlığı ve o başlıkla "
        "ilgili internetten toplanmış kaynak metinleri veriliyor. Görevin SADECE bu kaynaklara "
        "dayanarak, o alt başlık için 3-6 cümlelik, net ve doğru bir Türkçe özet yazmak. "
        "Kaynaklarda olmayan bilgiyi UYDURMA. Emin olmadığın yerlerde belirt. "
        "Sonunda kullandığın kaynakların başlıklarını parantez içinde listele."
    )
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
    system = (
        "Sen bir baş araştırmacısın. Sana bir konu hakkında farklı alt başlıklardan derlenmiş "
        "özetler veriliyor. Görevin bunları birleştirip; tekrarları kaldıran, çelişkileri "
        "belirten, akıcı, iyi organize edilmiş, Türkçe kapsamlı bir SONUÇ RAPORU yazmak. "
        "Rapor başlıklar halinde olsun, ama alt başlıkları kopyalamak yerine anlatıyı birleştir. "
        "En sonda 2-3 cümlelik genel bir değerlendirme/özet paragrafı ekle."
    )
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
