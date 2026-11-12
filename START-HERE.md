# Daybreak — uruchomienie

## Repo

Rozpakuj archiwum. Zawartość folderu `daybreak` wrzuć do swojego repo GitHub
(`pyproject.toml`, `install.sh`, `src` i pozostałe pliki mają być w katalogu głównym).
Ukryte `.gitignore` i `.github` również należą do repo. Nie dodawaj danych serwera.

## Serwer

Przez SSH na hoście (nie w kontenerze):

```bash
git clone TWOJ_ADRES_REPO daybreak
cd daybreak
sudo bash install.sh
sudo daybreak
```

Instalator zastępuje wcześniejszą usługę Daybreak. Zachowuje jej stare pliki,
ale nowa aplikacja zaczyna od własnej, pustej konfiguracji. Przy pierwszej
instalacji codzienna pobudka nie jest jeszcze włączona.

## Dalej tylko panel

1. **Connection**: URL i token → **Save connection** → **Choose phone**.
   **Pair NFC tag** → przyłóż telefon do nowego NFC → sprawdź identyfikatory →
   **Use captured tag** i potwierdź. Zapis zmienionego tagu wstrzymuje pobudki
   do ponownego testu i włączenia rutyny.
2. **Home Wi-Fi**: **Choose SSID sensor**, **Choose BSSID sensor** →
   **Use current Wi-Fi**, kiedy telefon jest w domu. Status ma być **Home**.
3. **Diagnostics**: **Run rolling test** → sprawdź realne dzwonienie i skan →
   **Confirm test passed**. Potem **Run Wi-Fi test** i potwierdzenie zachowania.
4. **System**: **Enable daily routine** oraz **Enable autostart**.

**Next wake-up** ustawia jeden dzień, **Routine** — regułę tygodniową.
Godzina 07:10 oznacza pierwszy alarm o 07:10. Kolejne alarmy powtarzają się
do potwierdzenia tagiem, także po restarcie usługi. Przed zapisaniem zobaczysz
pierwsze alarmy i odstępy między nimi.

Kolejne wersje wypychasz ze swojego komputera na GitHub, a na serwerze wybierasz
**System → Pull & deploy**. URL, token, tag, sieci i historia zostają na serwerze.
Nie musisz ponownie edytować plików ani wywoływać `systemctl`.

Nawigacja: **F1–F9**, **Tab**, **Shift-Tab**, **Enter**, **Esc**, mysz i przewijanie.
**Ctrl-Q** zamyka panel; usługa działa dalej. Zalecany terminal co najmniej 100×30.

Pozostaw w Companion **Persistent Connection → Always**. Stara automatyzacja
budzenia w HA ma pozostać wyłączona. Szczegóły i wymagania: [README.md](README.md).

## Przejście z QR na NFC

Utwórz nowy tag w HA i zapisz go na NTAG213. Użyj nowego identyfikatora,
a potem sparuj go w Connection poza trwającą pobudką lub testem. Wykonaj rolling
test z prawdziwym telefonem i ponownie włącz rutynę. Przełącz także odbiorniki
skanu w HA (w tym własne integracje otwierające Poranek). Dopiero po sprawdzeniu,
że nowy tag działa, a stary jest ignorowany, usuń stary wpis tagu i wydruk QR.
Samo usunięcie wpisu w HA nie wycofuje identyfikatora z Daybreaka ani integracji.
Stare ustawienia i historia są zachowane; techniczne nazwy pól `qr_*` są zgodne
z poprzednią wersją. Szczegóły i ograniczenia: README, „Moving from a printed QR to NFC”.
