// Plain-language explanations of every abbreviation the UI uses.

export const TERMS = {
  ATO: "Air Tasking Order: the list of every sortie planned for this 6-hour period.",
  CAP: "Combat Air Patrol: fighters guarding an area against enemy aircraft.",
  CAS: "Close Air Support: attacking enemy troops that are in contact with ours.",
  BAI: "Battlefield Air Interdiction: hitting enemy units just behind the front before they reach it.",
  SEAD: "Suppression of Enemy Air Defences: attacking SAM sites (here with Shrike anti-radar missiles) so the package can get through.",
  OCA: "Offensive Counter-Air: attacking enemy airfields and the aircraft on them.",
  QRA: "Quick Reaction Alert: armed fighters on standby, scrambled to intercept aircraft approaching the border.",
  ROE: "Rules of Engagement. Weapons tight (the crisis): fire only if fired upon. Weapons free (the war): engage any enemy aircraft.",
  BRAA: "Bearing, Range, Altitude, Aspect: where a contact is from you - e.g. 090/20, 15 thousand, hot (coming at you).",
  Bullseye: "A named reference point everyone uses for positions: 'bullseye 045/30' is 30 nm north-east of it. Here: Fulda.",
  MAGIC: "The ground radar controller (GCI) on your F10 radio menu: bogey dope, picture and threat calls. It only sees what our radars see.",
  Tension: "How close the crisis is to war (0-100). Incidents in the air push it up; seeing probes off calmly brings it down.",
  Escort: "Fighters flying with a strike package to protect it from enemy fighters.",
  Interdiction: "Attacks on supply lines - rail yards, convoys and depots - far behind the front.",
  Strike: "A bombing attack on a fixed target.",
  WSO: "Weapon Systems Officer: the F-4E's back-seater, who runs the radar and weapons.",
  SAM: "Surface-to-Air Missile site. The ring on the map is roughly how far it can reach.",
  AAA: "Anti-Aircraft Artillery: radar-guided guns, deadly at low altitude near the target.",
  EWR: "Early-Warning Radar: sees aircraft far across the front and directs fighters onto them.",
  NATO: "The Western alliance: here mainly US and West German forces. You fly for NATO.",
  WP: "Warsaw Pact: the Soviet-led alliance, here Soviet and East German forces.",
  FRG: "Federal Republic of Germany (West Germany), a NATO member.",
  GDR: "German Democratic Republic (East Germany), a Warsaw Pact member.",
  Z: "Zulu time = UTC/GMT. Briefings and DCS use it; local time in Germany in August is Z+2.",
  nm: "Nautical miles (1 nm = 1.852 km), the unit pilots use.",
  REFORGER: "Return of Forces to Germany: US divisions flown in from America to reinforce NATO.",
  Sortie: "One aircraft flying one mission.",
};

// "Z" only gets a tooltip where the UI asks for it explicitly (it appears in "Ctrl+Z" etc.)
const PATTERN = new RegExp(`\\b(${Object.keys(TERMS).filter((t) => t !== "Z").join("|")})\\b`, "g");

export function abbr(term, label = term) {
  const t = TERMS[term];
  return t ? `<abbr title="${t.replace(/"/g, "&quot;")}">${label}</abbr>` : label;
}

/** Wrap known terms in already-escaped HTML text with explanatory tooltips. */
export function annotate(escapedHtml) {
  return escapedHtml.replace(PATTERN, (m) => abbr(m));
}
