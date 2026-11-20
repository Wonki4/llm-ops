"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { VALIDATION_API, type RecipeValidation, type ValidationCreate, type ValidationPreview, validationTerminal } from "@/lib/recipe-validation";

export function useRecipeValidations(params: URLSearchParams, enabled = true) {
  return useQuery({ queryKey: ["recipe-validations", params.toString()],
    queryFn: () => apiFetch<{ items: RecipeValidation[]; total: number }>(`${VALIDATION_API}?${params}`), enabled, refetchInterval: 5000 });
}
export function useRecipeValidation(id: string) {
  return useQuery({ queryKey: ["recipe-validation", id], queryFn: () => apiFetch<RecipeValidation>(`${VALIDATION_API}/${id}`), enabled: !!id,
    refetchInterval: query => query.state.data && validationTerminal(query.state.data.status) ? false : 3000 });
}
export function useValidationPreview() {
  return useMutation({ mutationFn: (body: ValidationCreate) => apiFetch<ValidationPreview>(`${VALIDATION_API}/preview`, { method: "POST", body: JSON.stringify(body) }) });
}
export function useCreateValidation() {
  const client = useQueryClient();
  return useMutation({ mutationFn: (body: ValidationCreate) => apiFetch<RecipeValidation>(VALIDATION_API, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["recipe-validations"] }) });
}
export function useValidationAction(id: string, action: string) {
  const client = useQueryClient();
  return useMutation({ mutationFn: (body: object = {}) => apiFetch<RecipeValidation>(`${VALIDATION_API}/${id}/${action}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: data => { client.setQueryData(["recipe-validation", id], data); client.invalidateQueries({ queryKey: ["recipe-validations"] }); } });
}
