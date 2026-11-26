export function legacySettingsLocation(search:string,hash="") {
  const q=new URLSearchParams(search);const tab=q.get("tab");const team=q.get("team");let path="/admin/policies";
  if(tab==="serving-access" || (tab==="clusters" && hash==="#serving-grants")) {path=team?`/admin/teams/${encodeURIComponent(team)}`:"/admin/teams";q.set("tab","serving");}
  else if(tab==="clusters"){path="/admin/infrastructure";q.set("tab","clusters");}
  else q.set("tab",tab==="teams"?"teams":"keys");
  return `${path}?${q}${hash==="#serving-grants"?"":hash}`;
}
