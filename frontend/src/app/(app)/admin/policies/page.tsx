"use client";
import { Suspense } from "react";
import { AdminPage } from "@/components/admin-page";
import { AllocationPolicies } from "@/components/allocation-policies";
export default function Page(){return <AdminPage><Suspense><AllocationPolicies /></Suspense></AdminPage>;}
