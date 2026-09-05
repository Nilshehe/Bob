# BOB — ROADMAP med status

Legend: ✅ klart & verifierat mot riktig kod · ⚠️ delvis/scaffoldat men inte
klart kopplat · ❌ inte påbörjat · 🛑 se varning nedan

> **VIKTIG VARNING om Patch 1–5 (Event Queue, wake-meddelande, agent-greeting,
> memory-tool):** de byggdes mot en FELAKTIG rekonstruktion av `main.py`
> (skrapad från GitHubs webbvy, som visade sig vara ofullständig/inaktuell).
> Efter att jag laddade ner hela repot direkt (`codeload.github.com`) ser jag
> att det RIKTIGA repot redan har:
> - `funktioner/queue.py` — en egen `asyncio.Queue`-baserad event queue,
>   redan använd av `gui_server.py` för chattmeddelanden från GUI:t.
> - `funktioner/memory_store.py` — en färdig `LongTermMemory`-klass med
>   ChromaDB + GPU-lockad Ollama-embedding (`gpu_locked_embeddings`),
>   collection `bob_ltm`, `store()`/`query()`.
>
> Mina patchar 1–5 uppfann ALLTSÅ parallella, inkompatibla versioner
> (`threading.Queue` istället för `asyncio.Queue`, en egen `tools/memory.py`
> istället för att bygga vidare på `memory_store.py`). **De är därför INTE
> markerade som klara nedan** trots att jag "byggde" dem — de måste göras om
> mot den riktiga `main.py` (643 rader, som jag ännu inte läst i sin helhet)
> innan de räknas. Säg till om du vill att jag gör det som nästa steg.

---

## 1. Core AI & Context

### Conversation Buffer Memory
- [ ] 🛑 Restart/rensa aktuell buffer (byggd i patch 1, men mot fel main.py — måste göras om)
- [ ] Som tool
- [ ] Via Settings
- [ ] Starta om egen kontext utan att hela programmet startas om

### Context Monitor
- [ ] Modellens max context window
- [ ] Hur mycket som används / återstår / procent
- [ ] Aktuell tid + runtime-info som metadata i varje prompt

### Context Indicator
- [ ] Färgskala vit → varning vid 90/95/99% → auto-ny-context vid 100%
- [ ] Spara relevant info i memory innan omstart

---

## 2. Long-Term Memory
- [ ] 🛑 Temporärt minne m. 30-dagars expiration (byggd i patch 5 som `tools/memory.py`,
      men det finns redan en `funktioner/memory_store.py` — bör slås ihop dit istället)
- [ ] 🛑 Long-term memory (samma varning — bygg vidare på `memory_store.py`s `LongTermMemory`)
- [ ] 🛑 Memory tools: skapa/lista/söka/läsa/redigera/ta bort (byggda men mot fel grund)
- [ ] GUI:t visar alla sparade memories
- [ ] Automatic Memory Retrieval (embeddings/vector search utan stor modell, injiceras i prompten)

---

## 3. Event Queue
- [ ] 🛑 Events väcker inte Bob automatiskt (byggd i patch 1 mot fel main.py — gör om
      mot riktiga `funktioner/queue.py`)
- [ ] Pending Events/Notifications
- [ ] User Wake: kolla kö, sammanfatta, kort greeting, börja lyssna

---

## 4. Wake-Up System
- [ ] 🛑 Kombinera tid + events + agents + tools + mail + memories + kontext (patch 2–4,
      samma varning)
- [ ] Async Greeting som inte stör om användaren redan pratar

---

## 5. Bob Circle  ✅ (byggd mot RIKTIG kod denna gång — verifierad diff)
- [x] Ingen ansikte — visuell representation är en cirkel
- [x] Pulserar/andas i vila (`bob-breathe`-animation, alltid synlig nu, inte bara i Voice Mode)
- [ ] ⚠️ Rör sig subtilt när Bob arbetar/pratar — CSS-klassen `.active` finns,
      men INGET skickar den klassen än (skulle behöva koppla in på
      `agent_stream`/`agent_monitor_update` i app.js)
- [ ] ⚠️ Lyser upp när Bob vaknar — CSS-klassen `.woke` + `bob-wake-flash`-animation
      finns, men inget backend-event triggar den än (kräver en ny websocket-signal
      när wake-up-systemet i punkt 4 faktiskt körs — och den är inte klar, se ovan)
- [x] Bob Circle är en knapp — klick öppnar meny
- [x] Meny → **Apps**: lista + öppna Webbläsaren (via befintliga `create_html_component`)
- [x] Meny → **Widgets**: lista aktiva element + ta bort dem
- [x] Meny → **Developer Mode**: lista Bobs GUI-tools, fylla i args som JSON, köra manuellt
      (scoped till `gui_tools.py` hittills — Bobs vanliga agent-tools i main.py, t.ex.
      web_search/memory/code_ai, är en separat lista som inte är hopkopplad ännu)

---

## 6. Small Mode
- [ ] Mindre GUI, cirkel fortfarande synlig/klickbar, wake word fungerar, ingen förlorad funktionalitet

---

## 7. Support Writing
- [ ] Skriva + prata samtidigt istället för bara Voice Mode

---

## 8. Computer Control
- [ ] Mus/tangentbord/öppna program/interagera med skärmen
- [ ] Kräver Serious Mode

---

## 9. Approval AI & Computer Access
- [ ] Begäran om tidsbegränsad åtkomst, Accept/Reject

---

## 10. Serious Mode
- [ ] Rött GUI, dölj vanliga menyer, visa vad Bob gör/planerar/väntar på
- [ ] Stor röd Stop-knapp (Bob Circle blir stop-knappen)
- [ ] Bob kan pausa sig själv, timer stannar, Go/Resume

---

## 11. Planner
- [ ] AI Planning List (lägga till/ändra/markera klart, agents kan använda)
- [ ] User Planning (morgonfråga, tidsintervall-tolkning)

---

## 12. Timers
- [ ] Skapa/visa/hantera timers, syns i GUI

---

## 13. Bob Calendar
- [ ] Egen kalender + Google Calendar-toggle

---

## 14. Calculator Tool
- [ ] visible=true/false, Calculator ID (`calc_...`), samma ID-mönster för andra widgets

---

## 15. GUI & Widget System
- [x] Skapa/ta bort/lista/flytta/uppdatera widgets — **fanns redan** i det riktiga
      repot (`gui_tools.py`, 39 tools), inget jag behövde bygga

---

## 16. Settings App
- [ ] Egen app (finns en `config_widget` redan, men inte som fristående "app" nåbar
      från Bob Circle → Apps ännu)

---

## 17. Tool & Variable Registry
- [x] Fanns redan (`registry.py` + `bob_integration.py`) — Bob vet redan vilka
      GUI-tools/variabler som finns

---

## 18. Auto Mode
- [ ] OFF/ASSIST/AUTO

---

## 19. AI Providers
- [ ] NVIDIA API/NIM, Codex-integration, Claude Code-integration, gemensamt agentgränssnitt
      (config_manager.py har redan multi-provider-stöd för olika agenter — inte kollat i detalj)

---

## 20. Uppgradera AI-agenterna
- [ ] Code AI / Edit AI / Research AI-förbättringar + samarbete

---

## 21. Integrated Tools
- [ ] Maps, väder, aktuell information m.m. (mail/kalender/timers/filer delvis via
      befintliga tools, inte heltäckande)

---

## 22. Event-driven Background System
- [ ] Se punkt 3 — samma status/varning

---

## 23. Tool Repository & Update System (OPTIONAL)
- [ ] Inte påbörjad

---

## 24. Wake-up Information
- [ ] Se punkt 4

---

## 25. Övergripande arkitektur
- [~] Delvis på plats (GUI+backend+registry finns), men wake/event-flödet i
      arkitekturskissen är inte verifierat mot riktig kod än

---

## 26. Prioritet — sammanfattning

**PRIORITET 1 — Core**
- [ ] 🛑 Event Queue redesign / User-only wake / Pending notifications (gör om, se varning)
- [ ] 🛑 Conversation Buffer restart (gör om)
- [ ] Context tracking / Runtime metadata
- [x] Bob Circle
- [x] Klickbar Bob Circle-meny
- [ ] Small Mode

**PRIORITET 2 — Memory**
- [ ] 🛑 Allt (gör om ovanpå `funktioner/memory_store.py` istället för `tools/memory.py`)

**PRIORITET 3 — GUI & Tools**
- [ ] Calculator, Planner, Timers, Settings App
- [x] Tool Registry / Variable Registry (fanns redan)
- [x] Widget ID-system (fanns redan, t.ex. `status_...`-mönster i `gui_tools.py`)

**PRIORITET 4 — Agents**
- [ ] Inte påbörjad

**PRIORITET 5 — Computer Control**
- [ ] Inte påbörjad

**PRIORITET 6 — Services**
- [ ] Inte påbörjad

**Timers & Alarm**
- [ ] Inte påbörjad
