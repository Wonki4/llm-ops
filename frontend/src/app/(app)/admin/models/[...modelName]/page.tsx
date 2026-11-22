"use client";
import { useParams } from "next/navigation";
import { LegacyRedirect } from "@/components/legacy-redirect";
export default function Page(){const {modelName}=useParams<{modelName:string[]}>();return <LegacyRedirect href={`/admin/models/detail/${modelName.map(encodeURIComponent).join("/")}`} />;}
