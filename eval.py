import numpy as np
import nibabel as nib
import cc3d
import scipy
import os
import pandas as pd
import surface_distance
import sys

def dice(im1, im2):
    """
    Computes Dice score for two images

    Parameters
    ==========
    im1: Numpy Array/Matrix; Predicted segmentation in matrix form 
    im2: Numpy Array/Matrix; Ground truth segmentation in matrix form

    Output
    ======
    dice_score: Dice score between two images
    """

    im1 = np.asarray(im1).astype(bool)
    im2 = np.asarray(im2).astype(bool)

    if im1.shape != im2.shape:
        raise ValueError("Shape mismatch: im1 and im2 must have the same shape.")

    # Compute Dice coefficient
    intersection = np.logical_and(im1, im2)

    return 2. * intersection.sum() / (im1.sum() + im2.sum())

def get_TissueWiseSeg(prediction_matrix, gt_matrix, tissue_type):
    """
    Converts the segmentatations to isolate tissue types

    Parameters
    ==========
    prediction_matrix: Numpy Array/Matrix; Predicted segmentation in matrix form 
    gt_matrix: Numpy Array/Matrix; Ground truth segmentation in matrix form
    tissue_type: str; Can be WT, ET or TC

    Output
    ======
    prediction_matrix: Numpy Array/Matrix; Predicted segmentation in matrix form with 
                       just tissue type mentioned
    gt_matrix: Numpy Array/Matrix; Ground truth segmentation in matrix form with just 
                       tissue type mentioned
    """

    if tissue_type == 'WT':
        np.place(prediction_matrix, (prediction_matrix != 1) & (prediction_matrix != 2) & (prediction_matrix != 3), 0)
        np.place(prediction_matrix, (prediction_matrix > 0), 1)
        
        np.place(gt_matrix, (gt_matrix != 1) & (gt_matrix != 2) & (gt_matrix != 3), 0)
        np.place(gt_matrix, (gt_matrix > 0), 1)
    
    elif tissue_type == 'TC':
        np.place(prediction_matrix, (prediction_matrix != 1)  & (prediction_matrix != 3), 0)
        np.place(prediction_matrix, (prediction_matrix > 0), 1)
        
        np.place(gt_matrix, (gt_matrix != 1) & (gt_matrix != 3), 0)
        np.place(gt_matrix, (gt_matrix > 0), 1)
        
    elif tissue_type == 'ET':
        np.place(prediction_matrix, (prediction_matrix != 3), 0)
        np.place(prediction_matrix, (prediction_matrix > 0), 1)
        
        np.place(gt_matrix, (gt_matrix != 3), 0)
        np.place(gt_matrix, (gt_matrix > 0), 1)
    
    return prediction_matrix, gt_matrix


def get_GTseg_combinedByDilation(gt_dilated_cc_mat, gt_label_cc):
    """
    Computes the Corrected Connected Components after combing lesions
    together with respect to their dilation extent

    Parameters
    ==========
    gt_dilated_cc_mat: Numpy Array/Matrix; Ground Truth Dilated Segmentation 
                       after CC Analysis
    gt_label_cc: Numpy Array/Matrix; Ground Truth Segmentation after 
                       CC Analysis

    Output
    ======
    gt_seg_combinedByDilation_mat: Numpy Array/Matrix; Ground Truth 
                                   Segmentation after CC Analysis and 
                                   combining lesions
    """    
    
    
    gt_seg_combinedByDilation_mat = np.zeros_like(gt_dilated_cc_mat)

    for comp in range(np.max(gt_dilated_cc_mat)):  
        comp += 1

        gt_d_tmp = np.zeros_like(gt_dilated_cc_mat)
        gt_d_tmp[gt_dilated_cc_mat == comp] = 1
        gt_d_tmp = (gt_label_cc*gt_d_tmp)

        np.place(gt_d_tmp, gt_d_tmp > 0, comp)
        gt_seg_combinedByDilation_mat += gt_d_tmp
        
    return gt_seg_combinedByDilation_mat


def get_LesionWiseScores(prediction_seg, gt_seg, label_value):
    """
    Computes the Lesion-wise scores for pair of prediction and ground truth
    segmentations

    Parameters
    ==========
    prediction_seg: str; location of the prediction segmentation    
    gt_label_cc: str; location of the gt segmentation
    label_value: str; Can be WT, ET or TC

    Output
    ======
    tp: Number of TP lesions WRT prediction segmentation
    fn: Number of FN lesions WRT prediction segmentation
    fp: Number of FP lesions WRT prediction segmentation 
    gt_tp: Number of Ground Truth TP lesions WRT prediction segmentation 
    metric_pairs: list; All the lesion-wise metrics  
    full_dice: Dice Score of the pair of segmentations
    full_gt_vol: Total Ground Truth Segmenatation Volume
    full_pred_vol: Total Prediction Segmentation Volume
    """

    ## Get Prediction and GT segs matrix files
    pred_nii = nib.load(prediction_seg)
    gt_nii = nib.load(gt_seg)
    pred_mat = pred_nii.get_fdata()
    gt_mat = gt_nii.get_fdata()

    ## Get Spacing to computes volumes
    ## Brats Assumes all spacing is 1x1x1mm3
    sx, sy, sz = pred_nii.header.get_zooms()

    ## Get the prediction and GT matrix based on 
    ## WT, TC, ET

    pred_mat, gt_mat = get_TissueWiseSeg(
                                prediction_matrix = pred_mat,
                                gt_matrix = gt_mat,
                                tissue_type = label_value
                            )
    
    ## Get Dice score for the full image
    full_dice = dice(
                pred_mat, 
                gt_mat
            )
    
    ## Get Sensitivity and Specificity
    full_sens, full_specs = get_sensitivity_and_specificity(result_array = pred_mat, 
                                                            target_array = gt_mat)
    
    ## Get GT Volume and Pred Volume for the full image
    full_gt_vol = np.sum(gt_mat)*sx*sy*sz
    full_pred_vol = np.sum(pred_mat)*sx*sy*sz

    ## Performing Dilation and CC analysis

    dilation_struct = scipy.ndimage.generate_binary_structure(3, 2)

    gt_mat_cc = cc3d.connected_components(gt_mat, connectivity=26)
    pred_mat_cc = cc3d.connected_components(pred_mat, connectivity=26)

    gt_mat_dilation = scipy.ndimage.binary_dilation(gt_mat, structure = dilation_struct, iterations=1)
    gt_mat_dilation_cc = cc3d.connected_components(gt_mat_dilation, connectivity=26)

    gt_mat_combinedByDilation = get_GTseg_combinedByDilation(
                                                            gt_dilated_cc_mat = gt_mat_dilation_cc, 
                                                            gt_label_cc = gt_mat_cc
                                                        )
    

    ## Performing the Lesion-By-Lesion Comparison

    gt_label_cc = gt_mat_combinedByDilation
    pred_label_cc = pred_mat_cc

    gt_tp = []
    tp = []
    fn = []
    fp = []
    metric_pairs = []

    for gtcomp in range(np.max(gt_label_cc)):
        gtcomp += 1

        ## Extracting current lesion
        gt_tmp = np.zeros_like(gt_label_cc)
        gt_tmp[gt_label_cc == gtcomp] = 1

        # Volume of lesion
        gt_vol = np.sum(gt_tmp)*sx*sy*sz
        
        ## Extracting Predicted true positive lesions
        pred_tmp = np.copy(pred_label_cc)
        pred_tmp = pred_tmp*gt_tmp
        intersecting_cc = np.unique(pred_tmp) 
        intersecting_cc = intersecting_cc[intersecting_cc != 0] 
        for cc in intersecting_cc:
            tp.append(cc)

        ## Isolating Predited Lesions to calulcate Metrics
        pred_tmp = np.copy(pred_label_cc)
        pred_tmp[np.isin(pred_tmp,intersecting_cc,invert=True)] = 0
        pred_tmp[np.isin(pred_tmp,intersecting_cc)] = 1

        ## Calculating Lesion-wise Dice and HD95
        dice_score = dice(pred_tmp, gt_tmp)
        surface_distances = surface_distance.compute_surface_distances(gt_tmp, pred_tmp, (sx,sy,sz))
        hd = surface_distance.compute_robust_hausdorff(surface_distances, 95)

        metric_pairs.append((intersecting_cc, 
                            gtcomp, gt_vol, dice_score, hd))
        
        ## Extracting Number of TP/FP/FN and other data
        if len(intersecting_cc) > 0:
            gt_tp.append(gtcomp)
        else:
            fn.append(gtcomp)

    fp = np.unique(
            pred_label_cc[np.isin(
                pred_label_cc,tp+[0],invert=True)])
    
    return tp, fn, fp, gt_tp, metric_pairs, full_dice, full_gt_vol, full_pred_vol, full_sens, full_specs


def get_sensitivity_and_specificity(result_array, target_array):
    iC = np.sum(result_array)
    rC = np.sum(target_array)

    overlap = np.where((result_array == target_array), 1, 0)

    # Where they agree are both equal to that value
    TP = overlap[result_array == 1].sum()
    FP = iC - TP
    FN = rC - TP
    TN = np.count_nonzero((result_array != 1) & (target_array != 1))

    Sens = 1.0 * TP / (TP + FN + sys.float_info.min)
    Spec = 1.0 * TN / (TN + FP + sys.float_info.min)

    # Make Changes if both input and reference are 0 for the tissue type
    if (iC == 0) and (rC == 0):
        Sens = 1.0

    return Sens, Spec



def get_LesionWiseResults(pred_file, gt_file):
    """
    Computes the Lesion-wise scores for pair of prediction and ground truth
    segmentations

    Parameters
    ==========
    pred_file: str; location of the prediction segmentation    
    gt_file: str; location of the gt segmentation

    Output
    ======
    Saves the performance metrics as CSVs
    """
    
    final_lesionwise_metrics_df = pd.DataFrame()
    final_metrics_dict = dict()
    label_values = ['WT', 'TC', 'ET']

    for l in range(len(label_values)):
        tp, fn, fp, gt_tp, metric_pairs, full_dice, full_gt_vol, full_pred_vol, full_sens, full_specs = get_LesionWiseScores(
                                                            prediction_seg = pred_file,
                                                            gt_seg = gt_file,
                                                            label_value = label_values[l]
                                                        )
        
        metric_df = pd.DataFrame(
            metric_pairs, columns=['predicted_lesion_numbers', 'gt_lesion_numbers', 
                                   'gt_lesion_vol', 'dice_lesionwise', 'hd95_lesionwise']
                ).sort_values(by = ['gt_lesion_numbers'], ascending=True).reset_index(drop = True)
        
        metric_df['Label'] = [label_values[l]]*len(metric_df)
        metric_df = metric_df.replace(np.inf, 374)

        final_lesionwise_metrics_df = final_lesionwise_metrics_df.append(metric_df)
        metric_df_thresh5 = metric_df[metric_df['gt_lesion_vol'] > 5]

        try:
            lesion_wise_dice = np.sum(metric_df_thresh5['dice_lesionwise'])/(len(metric_df_thresh5) + len(fp))
        except:
            lesion_wise_dice = np.nan
            
        try:
            lesion_wise_hd95 = (np.sum(metric_df_thresh5['hd95_lesionwise']) + len(fp)*374)/(len(metric_df_thresh5) + len(fp))
        except:
            lesion_wise_hd95 = np.nan

        metrics_dict = {
            'Num_GT_TP' : len(gt_tp),
            'Num_TP' : len(tp),
            'Num_FP' : len(fp),
            'Num_FN' : len(fn),
            'Sensitivity': full_sens,
            'Specificity': full_specs,
            'Complete_Dice' : full_dice,
            'GT_Complete_Volume' : full_gt_vol,
            'LesionWise_Score_Dice' : lesion_wise_dice,
            'LesionWise_Score_HD95' : lesion_wise_hd95
        }

        final_metrics_dict[label_values[l]] = metrics_dict


    #final_lesionwise_metrics_df.to_csv(os.path.split(pred_file)[0] + '/' +
    #                                   os.path.split(pred_file)[1].split('.')[0] + 
    #                                   '_lesionwise_metrics.csv',
    #                                   index=False)
    
    results_df = pd.DataFrame(final_metrics_dict).T
    results_df['Labels'] = results_df.index
    results_df = results_df.reset_index(drop=True)
    results_df.insert(0, 'Labels', results_df.pop('Labels'))

    
    results_df.to_csv(
                    os.path.split(pred_file)[0] + '/' +
                    os.path.split(pred_file)[1].split('.')[0] + 
                    '_results.csv', index=False)
    
