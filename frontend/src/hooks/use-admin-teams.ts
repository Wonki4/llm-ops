"use client";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
export interface TeamAccess {
  team_id: string; team_alias: string; mode: "none" | "all" | "custom"; models: string[]; selection: string[];
  available_models: string[]; available_groups: Record<string,string[]>;
  inherited_groups: {access_group_id:string;access_group_name:string;models:string[]}[];
  effective_models: {model_name:string;via:string[]}[];
}
export interface AdminTeam {team_id:string;team_alias:string;member_count:number;spend:number;max_budget:number|null;access:TeamAccess}
export function useAdminTeams(params: {q?:string;page?:number;model_name?:string;access_group?:string}) {
  const search = new URLSearchParams(Object.entries(params).filter(([,v])=>v!==undefined).map(([k,v])=>[k,String(v)]));
  return useQuery({queryKey:["admin-teams", search.toString()],queryFn:()=>apiFetch<{teams:AdminTeam[];total:number;page_size:number}>(`/api/admin/teams?${search}`)});
}
export function useTeamAccess(id:string) {return useQuery({queryKey:["team-access",id],queryFn:()=>apiFetch<TeamAccess>(`/api/admin/teams/${encodeURIComponent(id)}/access`)});}
