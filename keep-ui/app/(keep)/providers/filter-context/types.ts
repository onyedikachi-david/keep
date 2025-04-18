import { Dispatch, SetStateAction } from "react";
import { TProviderCategory, TProviderLabels } from "@/shared/api/providers";

export interface IFilterContext {
  providersSearchString: string;
  providersSelectedTags: TProviderLabels[];
  providersSelectedCategories: TProviderCategory[];
  setProvidersSearchString: Dispatch<SetStateAction<string>>;
  setProvidersSelectedTags: Dispatch<SetStateAction<TProviderLabels[]>>;
  setProvidersSelectedCategories: Dispatch<SetStateAction<TProviderCategory[]>>;
}
