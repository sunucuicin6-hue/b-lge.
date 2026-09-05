# Derin Araştırma

Bir konuyu (örn. "Zodiac Katili") otomatik olarak alt başlıklara bölüp, her birini
internetten araştırıp, sonunda hepsini birleştiren tek sayfalık bir "deep research" sistemi.

## Nasıl çalışıyor

1. **Planlama** — küçük bir LLM (varsayılan: `Qwen/Qwen2.5-7B-Instruct`, Hugging Face üzerinden)
   konuyu 5-7 alt araştırma başlığına böler (kurbanlar, deliller, kitaplar, medya, şüpheliler,
   tarihçe gibi — ama konuya göre otomatik uyarlanır).
2. **Araştırma** — her başlık için DuckDuckGo'da arama yapılır (API key gerekmez), bulunan
   sayfaların gerçek metni çekilir.
3. **Özetleme** — her başlık, o kaynaklara dayanarak LLM tarafından özetlenir.
4. **Sentez** — tüm özetler birleştirilip kapsamlı bir Türkçe sonuç raporu yazılır.

Frontend, backend'den gelen sonuçları başlık başlık **canlı** (streaming) olarak gösterir.

## Kurulum

### 1) Hugging Face API key al

- https://huggingface.co/settings/tokens adresinden bir **Read** token oluştur.
- ⚠️ Bu key'i hiçbir yerde (chat, kod paylaşımı, GitHub) açık şekilde paylaşma. Sızarsa hemen
  revoke edip yenisini oluştur.

### 2) Backend'i kur

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# .env dosyasını açıp HF_TOKEN=... satırına kendi key'ini yapıştır
```

Çalıştır:

```bash
python main.py
# ya da: uvicorn main:app --reload --port 8000
```

Backend şurada çalışır: `http://localhost:8000`
Test: `http://localhost:8000/api/health` → `{"status":"ok"}` dönmeli.

### 3) Frontend'i aç

`frontend/index.html` dosyasını doğrudan tarayıcıda aç (çift tıkla) ya da basit bir
sunucuyla servis et:

```bash
cd frontend
python3 -m http.server 5500
```

Sonra tarayıcıda `http://localhost:5500` adresine git.

> Not: `frontend/index.html` içindeki `API_BASE` değişkeni `http://localhost:8000` olarak
> ayarlı. Backend'i başka bir adreste çalıştırırsan bunu güncelle.

## Model değiştirme

`.env` dosyasındaki `HF_MODEL` değerini değiştirerek farklı bir model deneyebilirsin.
10B parametre altı ve HF ücretsiz kotasında (hf-inference / auto provider) çalışan modeller:

- `Qwen/Qwen2.5-7B-Instruct` (varsayılan, Türkçe kalitesi iyi)
- `meta-llama/Llama-3.1-8B-Instruct`
- `mistralai/Mistral-7B-Instruct-v0.3`

Farklı modelleri önce https://huggingface.co/playground üzerinde Türkçe bir prompt ile
deneyip karşılaştırman önerilir.

## Sınırlamalar / bilinen notlar

- Küçük (7-8B) modeller bazen JSON formatını tam istenildiği gibi döndürmeyebilir;
  `research_engine.py` içindeki `_extract_json_list()` fonksiyonu buna karşı bir
  toparlama mekanizması içerir, ama %100 garanti değildir.
- Bazı siteler `trafilatura` ile metin çıkarımına izin vermeyebilir (paywall, JS-heavy
  sayfalar); bu durumda o kaynak için sadece arama motoru özeti (snippet) kullanılır.
- DuckDuckGo arama hız sınırlarına takılabilir; çok sık/art arda istekte "arama hatası"
  görürsen birkaç saniye bekleyip tekrar dene.
- Bu bir prototip; canlıya (production) almadan önce CORS ayarlarını (`main.py` içinde
  `allow_origins=["*"]`) kendi domain'inle sınırla ve HF_TOKEN'ı asla frontend koduna koyma.
